"""Slice B: evidence records and store. Pure software-correctness tests.

NOTE ON FIXTURES: the 'cited' records below use an obviously synthetic source id
('fixture:not-a-real-source'). They exist only to exercise validation logic. They are not data,
are not shipped in examples, and must never be copied into a real evidence file.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from ecobackfire.contracts.common import ILLUSTRATIVE_MARKER, IntervalUncertainty, ProvenanceClass
from ecobackfire.contracts.experiment import Binding
from ecobackfire.errors import EvidenceValidationError, MissingEvidenceError
from ecobackfire.evidence import (
    EvidenceRecord,
    EvidenceStore,
    EvidenceTier,
    ReviewRecord,
    Scope,
    SourceRef,
    classify_tier,
    completeness,
    evidence_backed_issues,
    is_evidence_backed,
)
from ecobackfire.models.registry import get_model

pytestmark = pytest.mark.software

SRC = SourceRef(source_id="fixture:not-a-real-source", citation="Synthetic test citation (not real)")
REVIEW = ReviewRecord(reviewer="test reviewer", reviewed_on="2026-01-02", statement="fixture review")


def rec(**kw: object) -> EvidenceRecord:
    base: dict[str, object] = {
        "record_id": "r1",
        "variable_id": "f",
        "description": "fixture value",
        "value": 0.4,
        "unit": "kgCO2e/kWh",
        "provenance_class": ProvenanceClass.SOURCED,
        "source": SRC,
    }
    base.update(kw)
    return EvidenceRecord.model_validate(base)


def illustrative(**kw: object) -> EvidenceRecord:
    return rec(provenance_class=ProvenanceClass.ILLUSTRATIVE, source=None, note=ILLUSTRATIVE_MARKER, **kw)


# ----------------------------------------------------------------- record integrity
def test_sourced_requires_source() -> None:
    with pytest.raises(ValidationError, match="require a source"):
        rec(source=None)


def test_sourced_requires_citation_or_url() -> None:
    with pytest.raises(ValidationError, match="citation or a url"):
        rec(source=SourceRef(source_id="fixture:x"))


@pytest.mark.parametrize("sid", ["TBD", "todo-source", "placeholder", "fake_source", "N/A", "  ", "example"])
def test_placeholder_source_ids_rejected(sid: str) -> None:
    with pytest.raises(ValidationError):
        SourceRef(source_id=sid, citation="c")


@pytest.mark.parametrize("url", ["ftp://x.org/a", "www.x.org", "not a url", "doi:10.12/x"])
def test_bad_urls_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        SourceRef(source_id="fixture:x", url=url)


def test_good_urls_accepted() -> None:
    SourceRef(source_id="fixture:x", url="https://example.org/data.csv")
    SourceRef(source_id="fixture:x", url="doi:10.1234/abcd.5678")


def test_bad_dates_rejected() -> None:
    with pytest.raises(ValidationError):
        SourceRef(source_id="fixture:x", citation="c", retrieved_on="12/31/2025")
    with pytest.raises(ValidationError):
        ReviewRecord(reviewer="a", reviewed_on="yesterday", statement="s")


def test_illustrative_needs_marker_and_cannot_cite() -> None:
    with pytest.raises(ValidationError, match="note"):
        rec(provenance_class=ProvenanceClass.ILLUSTRATIVE, source=None)
    with pytest.raises(ValidationError, match="must not cite"):
        rec(provenance_class=ProvenanceClass.ILLUSTRATIVE, note=ILLUSTRATIVE_MARKER)


def test_only_cited_records_can_be_reviewed() -> None:
    with pytest.raises(ValidationError, match="only measured/sourced"):
        illustrative(review=REVIEW)


def test_value_must_be_inside_own_interval_and_finite() -> None:
    with pytest.raises(ValidationError, match="inside"):
        rec(uncertainty=IntervalUncertainty(low=0.5, high=0.6))
    with pytest.raises(ValidationError):
        rec(value=float("nan"))
    with pytest.raises(ValidationError):
        rec(value=float("inf"))


def test_record_id_no_whitespace_and_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        rec(record_id="has space")
    with pytest.raises(ValidationError):
        rec(surprise=1)


# ----------------------------------------------------------------- tiers and completeness
def test_tier_classification() -> None:
    assert classify_tier(illustrative()) is EvidenceTier.ILLUSTRATIVE
    assert classify_tier(rec(provenance_class=ProvenanceClass.USER_SUPPLIED, source=None)) is EvidenceTier.ASSUMPTION
    assert classify_tier(rec(provenance_class=ProvenanceClass.ESTIMATED, source=None)) is EvidenceTier.ASSUMPTION
    assert classify_tier(rec(provenance_class=ProvenanceClass.SIMULATED, source=None)) is EvidenceTier.SYNTHETIC
    assert classify_tier(rec()) is EvidenceTier.CITED_UNREVIEWED
    assert classify_tier(rec(review=REVIEW)) is EvidenceTier.CITED_REVIEWED


def test_completeness_counts_documentation_not_truth() -> None:
    score, missing = completeness(rec())
    assert score == pytest.approx(2 / 7)  # source + citation_or_url only
    assert set(missing) == {"locator", "geography", "period", "uncertainty_characterized", "human_review"}
    full = rec(
        source=SourceRef(source_id="fixture:x", citation="c", locator="table 1"),
        scope=Scope(geography="fixture-region", period="2000"),
        uncertainty=IntervalUncertainty(low=0.3, high=0.5),
        review=REVIEW,
    )
    assert completeness(full) == (1.0, [])


def test_evidence_backed_requires_review_scope_and_uncertainty() -> None:
    assert not is_evidence_backed(rec())
    assert not is_evidence_backed(illustrative())
    partial = rec(review=REVIEW)
    reasons = evidence_backed_issues(partial)
    assert any("geographic" in r for r in reasons) and any("temporal" in r for r in reasons)
    full = rec(
        scope=Scope(geography="fixture-region", period="2000"),
        uncertainty=IntervalUncertainty(low=0.3, high=0.5),
        review=REVIEW,
    )
    assert is_evidence_backed(full) and evidence_backed_issues(full) == []


def test_illustrative_can_never_be_evidence_backed_whatever_else_is_filled() -> None:
    r = illustrative(
        scope=Scope(geography="g", period="p"), uncertainty=IntervalUncertainty(low=0.3, high=0.5)
    )
    assert not is_evidence_backed(r)


# ----------------------------------------------------------------- store
def _write(path, records):  # type: ignore[no-untyped-def]
    path.write_text(json.dumps({"schema_version": "1.0.0", "records": records}), encoding="utf-8")


def test_store_roundtrip_hash_and_order_independence(tmp_path) -> None:  # type: ignore[no-untyped-def]
    a, b = rec(record_id="a"), illustrative(record_id="b", variable_id="E0", value=3000.0, unit="kWh/yr")
    docs = [r.model_dump(mode="json") for r in (a, b)]
    p1, p2 = tmp_path / "1.json", tmp_path / "2.json"
    _write(p1, docs)
    _write(p2, list(reversed(docs)))
    s1, s2 = EvidenceStore.from_file(p1), EvidenceStore.from_file(p2)
    assert len(s1) == 2 and s1.ids() == ["a", "b"]
    assert s1.sha256() == s2.sha256()
    assert s1.get("a") == a
    assert [r.record_id for r in s1.records_for("E0")] == ["b"]
    changed = rec(record_id="a", value=0.41)
    assert EvidenceStore([changed, b]).sha256() != s1.sha256()


def test_store_missing_record_raises_missing_evidence() -> None:
    with pytest.raises(MissingEvidenceError):
        EvidenceStore().get("nope")


def test_empty_store_is_allowed_in_memory() -> None:
    assert len(EvidenceStore()) == 0


def test_duplicate_ids_rejected() -> None:
    with pytest.raises(EvidenceValidationError, match="duplicate"):
        EvidenceStore([rec(), rec()])


def test_file_errors(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(EvidenceValidationError, match="not found"):
        EvidenceStore.from_file(tmp_path / "missing.json")
    empty = tmp_path / "empty.json"
    empty.write_text("  \n")
    with pytest.raises(EvidenceValidationError, match="empty"):
        EvidenceStore.from_file(empty)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(EvidenceValidationError, match="invalid JSON"):
        EvidenceStore.from_file(bad)
    wrong_keys = tmp_path / "keys.json"
    wrong_keys.write_text(json.dumps({"records": []}))
    with pytest.raises(EvidenceValidationError, match="exactly the keys"):
        EvidenceStore.from_file(wrong_keys)
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"schema_version": "0.9", "records": []}))
    with pytest.raises(EvidenceValidationError, match="schema_version"):
        EvidenceStore.from_file(old)
    badrec = tmp_path / "badrec.json"
    _write(badrec, [{"record_id": "x"}])
    with pytest.raises(EvidenceValidationError, match="record #0"):
        EvidenceStore.from_file(badrec)


# ----------------------------------------------------------------- binding consistency
def _binding(**kw: object) -> Binding:
    base: dict[str, object] = {
        "value": 0.4,
        "unit": "kgCO2e/kWh",
        "provenance_class": ProvenanceClass.SOURCED,
        "evidence_record_id": "r1",
    }
    base.update(kw)
    return Binding.model_validate(base)


def _codes(store: EvidenceStore, b: Binding, vid: str = "f") -> set[str]:
    spec = get_model("efficiency_rebound@1.0.0").spec
    return {i.code for i in store.check_binding(vid, b, spec.input_by_id(vid).canonical_unit)}


def test_binding_consistent_with_reviewed_record_has_no_issues() -> None:
    assert _codes(EvidenceStore([rec(review=REVIEW)]), _binding()) == set()


def test_binding_to_unreviewed_record_warns() -> None:
    assert _codes(EvidenceStore([rec()]), _binding()) == {"W_UNREVIEWED"}


def test_binding_unit_conversion_is_honoured() -> None:
    store = EvidenceStore([rec(value=400.0, unit="gCO2e/kWh", review=REVIEW)])
    assert _codes(store, _binding(value=0.4, unit="kgCO2e/kWh")) == set()


def test_binding_mismatches_are_errors() -> None:
    store = EvidenceStore([rec(review=REVIEW)])
    assert _codes(store, _binding(value=0.5)) == {"E_VALUE_MISMATCH"}
    assert _codes(store, _binding(evidence_record_id="ghost")) == {"E_RECORD_NOT_FOUND"}
    assert _codes(store, _binding(), vid="E0") >= {"E_VARIABLE_MISMATCH"}
    assert _codes(store, _binding(provenance_class=ProvenanceClass.MEASURED)) == {"E_CLASS_MISMATCH"}
    assert _codes(store, _binding(unit="kWh/yr")) == {"E_UNIT"}


def test_binding_cannot_upgrade_an_illustrative_record_to_sourced() -> None:
    store = EvidenceStore([illustrative(record_id="r1")])
    assert "E_CLASS_MISMATCH" in _codes(store, _binding())


def test_binding_without_record_and_not_cited_has_no_issues() -> None:
    b = Binding(value=0.4, unit="kgCO2e/kWh", provenance_class=ProvenanceClass.USER_SUPPLIED)
    assert _codes(EvidenceStore(), b) == set()
