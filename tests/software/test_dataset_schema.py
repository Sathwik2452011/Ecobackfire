"""Dataset release schema. Pure software-correctness tests on SYNTHETIC fixtures (see _dataset_fixtures)."""

from __future__ import annotations

import pytest
from _dataset_fixtures import SYN_REVIEW, evidence, illustrative_evidence, record, release
from pydantic import ValidationError

from ecobackfire.contracts.common import ProvenanceClass
from ecobackfire.datasets.schema import DatasetRelease, LicenseInfo, QualityFlag, RawFile
from ecobackfire.datasets.status import EvidenceStatus, status_from_provenance

pytestmark = pytest.mark.software


def load(**over: object) -> DatasetRelease:
    return DatasetRelease.model_validate(release(**over))


def est(rid: str, parents: list[str], variable: str = "x") -> dict[str, object]:
    ev = evidence(rid, variable, 1.0, "dimensionless", provenance_class="estimated", source=None)
    return record(ev, derived_from=parents, derivation="synthetic derivation")


def test_valid_release_loads() -> None:
    rel = load()
    assert rel.dataset_id == "synthetic-fixture" and len(rel.records) == 1
    assert rel.records[0].status() is EvidenceStatus.SOURCED_UNREVIEWED


def test_every_provenance_class_has_a_status() -> None:
    for pc in ProvenanceClass:
        status_from_provenance(pc, reviewed=False)  # must not raise


def test_reviewed_sourced_record_is_verified_source() -> None:
    rel = load(records=[record(evidence(review=SYN_REVIEW))])
    assert rel.records[0].status() is EvidenceStatus.VERIFIED_SOURCE


def test_illustrative_record_status() -> None:
    rel = load(records=[record(illustrative_evidence())])
    assert rel.records[0].status() is EvidenceStatus.ILLUSTRATIVE


def test_quality_and_methodology_inheritance() -> None:
    rel = load(
        methodology="release method",
        quality="accepted",
        records=[record(evidence("a")), record(evidence("b"), quality="deprecated", methodology="own method")],
    )
    a, b = rel.records
    assert rel.effective_quality(a) is QualityFlag.ACCEPTED and rel.effective_quality(b) is QualityFlag.DEPRECATED
    assert rel.effective_methodology(a) == "release method" and rel.effective_methodology(b) == "own method"


def test_withdrawn_release_withdraws_all_records() -> None:
    rel = load(quality="withdrawn", records=[record(evidence("a"), quality="accepted")])
    assert rel.effective_quality(rel.records[0]) is QualityFlag.WITHDRAWN


@pytest.mark.parametrize(
    "over, match",
    [
        ({"dataset_id": "Bad Id"}, "invalid dataset_id"),
        ({"release_version": "1.0"}, "invalid release version"),
        ({"release_version": "1.0.0", "parent_release": "1.0.0"}, "earlier version"),
        ({"release_version": "1.0.0", "parent_release": "2.0.0"}, "earlier version"),
        ({"last_updated": "2026-13-01"}, "last_updated"),
        ({"title": "  "}, "title must be non-empty"),
        ({"records": []}, "at least one record"),
        ({"schema_version": "9.9.9"}, "unsupported dataset schema_version"),
        ({"surprise": 1}, "surprise"),
        ({"records": [record(evidence("a")), record(evidence("a"))]}, "duplicate record_id"),
        (
            {"records": [record(evidence("a", "f"))], "missing_variables": [{"variable_id": "f", "reason": "r"}]},
            "missing but also present",
        ),
    ],
)
def test_invalid_release_rejected(over: dict[str, object], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        load(**over)


def test_release_with_only_documented_missing_variable_is_valid() -> None:
    rel = load(records=[], missing_variables=[{"variable_id": "epsilon", "reason": "no suitable source found"}])
    assert rel.missing_variables[0].variable_id == "epsilon"


@pytest.mark.parametrize(
    "kw, match",
    [
        ({"geography_codes": ["Atlantis"]}, "invalid geography code"),
        ({"geography_codes": ["IN", "in"]}, "duplicate geography codes"),
        ({"temporal": {"start": "2022", "end": "2021"}}, "before start"),
        ({"temporal": {"start": "2022-13", "end": "2022"}}, "valid calendar date"),
    ],
)
def test_invalid_scope_metadata_rejected(kw: dict[str, object], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        load(records=[record(**kw)])


def test_geography_codes_are_normalized() -> None:
    rel = load(records=[record(geography_codes=["in-ap", "global"], temporal={"start": "2020", "end": "2022-06"})])
    assert rel.records[0].geography_codes == ("IN-AP", "GLOBAL")
    assert rel.records[0].temporal is not None and rel.records[0].temporal.period.end.isoformat() == "2022-06-30"


def test_measured_and_sourced_cannot_claim_derivation() -> None:
    with pytest.raises(ValidationError, match="must not carry derived_from"):
        load(records=[record(evidence("a"), derived_from=["b"], derivation="x"), record(evidence("b"))])


def test_estimated_requires_derivation_and_parents() -> None:
    ev = evidence("a", "x", 1.0, "dimensionless", provenance_class="estimated", source=None)
    with pytest.raises(ValidationError, match="documented derivation"):
        load(records=[record(ev)])
    with pytest.raises(ValidationError, match="require derived_from"):
        load(records=[record(ev, derivation="d")])


def test_derivation_valid_from_weaker_or_equal_parent_class() -> None:
    rel = load(records=[record(evidence("p")), est("c", ["p"])])
    assert rel.records[1].status() is EvidenceStatus.DERIVED


def test_derivation_cannot_promote_illustrative_parent() -> None:
    with pytest.raises(ValidationError, match="cannot promote"):
        load(records=[record(illustrative_evidence("p", "x", 1.0)), est("c", ["p"])])


def test_unknown_parent_self_and_cycle_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown record"):
        load(records=[est("c", ["nope"])])
    with pytest.raises(ValidationError, match="from itself"):
        load(records=[est("c", ["c"])])
    with pytest.raises(ValidationError, match="cycle"):
        load(records=[est("a", ["b"], "x"), est("b", ["a"], "y")])


@pytest.mark.parametrize("path", ["/abs/file.csv", "../up.csv", "a/../b.csv", "a\\b.csv", ""])
def test_raw_file_path_rules(path: str) -> None:
    with pytest.raises(ValidationError, match="relative POSIX path"):
        RawFile(source_id="s", path=path, sha256="0" * 64)


def test_raw_file_hash_rules_and_duplicates() -> None:
    with pytest.raises(ValidationError, match="64 lower-case hex"):
        RawFile(source_id="s", path="a.csv", sha256="ABC")
    raw = {"source_id": "s", "path": "a.csv", "sha256": "0" * 64}
    with pytest.raises(ValidationError, match="duplicate raw_files"):
        load(raw_files=[raw, raw])


def test_license_rules() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        LicenseInfo()
    with pytest.raises(ValidationError, match="license url"):
        LicenseInfo(identifier="X", url="not a url")
    assert LicenseInfo(text="see provider terms").redistribution_allowed is None  # unknown is not "allowed"
