"""Evidence-backed parameter resolution: rules of ``datasets.resolution`` on SYNTHETIC registries.

All fixtures are synthetic (see _resolution_fixtures). They test resolver behaviour only; they are not data about
any real grid, device or country.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from _resolution_fixtures import DS, VER, build_registry, f_record, request

from ecobackfire.contracts.resolution import EvidenceRequest
from ecobackfire.datasets.resolution import (
    AGREEING_SELECTION_RULE,
    binding_from_resolution,
    resolve_parameter,
    resolve_requests,
)
from ecobackfire.errors import ConfigError, EvidenceConflictError, MissingEvidenceError
from ecobackfire.experiments.evidence import materialize_bindings
from ecobackfire.models.registry import get_model

pytestmark = pytest.mark.software
SPEC = get_model("efficiency_rebound@1.0.0").spec


def _resolve(reg, **req):  # type: ignore[no-untyped-def]
    return resolve_parameter(reg, SPEC, "f", EvidenceRequest(**request(**req)))


def _codes(res):  # type: ignore[no-untyped-def]
    return {e.evidence_id.split(":")[-1]: [r.code for r in e.reasons] for e in res.excluded}


# ---------------------------------------------------------------- success, units, scope
def test_successful_resolution_carries_the_full_chain(tmp_path: Path) -> None:
    reg = build_registry(tmp_path, [f_record()])
    res = _resolve(reg)
    assert res.state == "resolved" and res.basis == "evidence"
    s = res.selected
    assert s is not None
    assert s.evidence_id == f"{DS}@{VER}:f.in.2024"
    assert (s.dataset_id, s.version, s.record_id) == (DS, VER, "f.in.2024")
    assert s.source is not None and s.source.source_id == "fixture:not-a-real-source"
    assert s.status == "sourced_unreviewed" and s.reviewed is False
    assert s.geography_match == "exact" and s.temporal_match == "contains"
    assert s.release_file_sha256 == reg.get(DS, VER).file_sha256
    assert s.release_content_sha256 == reg.get(DS, VER).content_sha256
    assert res.selection_rule == "single_applicable_record"
    assert any("not evidence-backed" in w for w in res.warnings)  # unreviewed + unknown uncertainty


def test_unit_conversion_is_recorded_and_original_unit_preserved(tmp_path: Path) -> None:
    reg = build_registry(tmp_path, [f_record(value=705.4, unit="gCO2e/kWh")])
    res = _resolve(reg)
    s = res.selected
    assert s is not None
    c = s.conversion
    assert (c.original_value, c.original_unit) == (705.4, "gCO2e/kWh")
    assert (c.canonical_unit, c.performed) == ("kgCO2e/kWh", True)
    assert c.canonical_value == pytest.approx(0.7054, rel=1e-12) and c.factor == pytest.approx(1e-3, rel=1e-12)
    assert (s.value, s.unit) == (705.4, "gCO2e/kWh")  # the record's own value is untouched
    b = binding_from_resolution(res)
    assert (b.value, b.unit) == (705.4, "gCO2e/kWh") and b.evidence_record_id == s.evidence_id


def test_canonical_unit_record_performs_no_conversion(tmp_path: Path) -> None:
    reg = build_registry(tmp_path, [f_record(value=0.5, unit="kgCO2e/kWh")])
    c = _resolve(reg).selected.conversion  # type: ignore[union-attr]
    assert c.performed is False and c.canonical_value == 0.5


def test_interval_uncertainty_becomes_a_canonical_range(tmp_path: Path) -> None:
    unc = {"kind": "interval", "low": 600.0, "high": 800.0}
    reg = build_registry(tmp_path, [f_record(uncertainty=unc)])
    s = _resolve(reg).selected
    assert s is not None and s.range_canonical is not None
    assert s.range_canonical == pytest.approx((0.6, 0.8))
    assert not any("no uncertainty interval" in w for w in _resolve(reg).warnings)


def test_unknown_uncertainty_gives_no_range_and_invents_none(tmp_path: Path) -> None:
    s = _resolve(build_registry(tmp_path, [f_record()])).selected
    assert s is not None and s.range_canonical is None and s.uncertainty.kind == "unknown"


@pytest.mark.parametrize("bad_unit", ["kg", "m", "foo/bar", "kWh/yr"])
def test_incompatible_or_unknown_units_are_rejected(tmp_path: Path, bad_unit: str) -> None:
    reg = build_registry(tmp_path, [f_record(unit=bad_unit)])
    res = _resolve(reg)
    assert res.state == "missing" and res.selected is None
    assert _codes(res) == {"f.in.2024": ["unit_incompatible"]}
    with pytest.raises(MissingEvidenceError, match="unit_incompatible"):
        materialize_bindings(SPEC, {}, {"f": EvidenceRequest(**request())}, reg)


def test_one_incompatible_record_does_not_hide_a_good_one(tmp_path: Path) -> None:
    reg = build_registry(tmp_path, [f_record("bad", unit="kg"), f_record("good")])
    res = _resolve(reg)
    assert res.state == "resolved" and res.selected.record_id == "good"  # type: ignore[union-attr]
    assert _codes(res) == {"bad": ["unit_incompatible"]}


def test_physical_bound_violation_excludes_the_record(tmp_path: Path) -> None:
    res = _resolve(build_registry(tmp_path, [f_record(value=-5.0)]))
    assert res.state == "missing" and _codes(res) == {"f.in.2024": ["violates_physical_bounds"]}


# ---------------------------------------------------------------- missing evidence
def test_missing_variable_dataset_and_version_are_explicit_states(tmp_path: Path) -> None:
    reg = build_registry(tmp_path, [f_record(variable_id="L", value=5.0, unit="yr")])
    no_var = _resolve(reg)
    assert no_var.state == "missing" and no_var.selected is None and no_var.applicable == []
    gone_v = _resolve(reg, version="9.9.9")
    gone_d = _resolve(reg, dataset_id="no-such-dataset")
    for r in (gone_v, gone_d):
        assert r.state == "missing" and r.excluded[0].reasons[0].code == "dataset_version_not_found"
    with pytest.raises(MissingEvidenceError):
        binding_from_resolution(no_var)


def test_missing_evidence_never_falls_back_to_an_existing_illustrative_binding(tmp_path: Path, e2_config) -> None:  # type: ignore[no-untyped-def]
    reg = build_registry(tmp_path, [f_record(geo="FR")])  # nothing for IN
    assert "f" in e2_config.bindings  # an illustrative f exists...
    with pytest.raises(MissingEvidenceError):  # ...and is NOT silently used
        materialize_bindings(SPEC, e2_config.bindings, {"f": EvidenceRequest(**request())}, reg)


def test_select_of_a_non_applicable_record_is_missing_not_substituted(tmp_path: Path) -> None:
    reg = build_registry(tmp_path, [f_record("a"), f_record("b", geo="FR")])
    res = _resolve(reg, select=f"{DS}@{VER}:b")
    assert res.state == "missing" and res.selected is None
    assert any(r.code == "selected_record_not_applicable" for e in res.excluded for r in e.reasons)


# ---------------------------------------------------------------- geography
def test_geographic_mismatch_is_excluded(tmp_path: Path) -> None:
    res = _resolve(build_registry(tmp_path, [f_record(geo="FR")]))
    assert res.state == "missing" and _codes(res) == {"f.in.2024": ["geography_mismatch"]}


def test_global_record_is_not_used_for_a_country_unless_fallback_is_requested(tmp_path: Path) -> None:
    reg = build_registry(tmp_path, [f_record("world", geo="GLOBAL")])
    strict = _resolve(reg)
    assert strict.state == "missing" and _codes(strict) == {"world": ["geography_fallback_not_permitted"]}
    loose = _resolve(reg, geography_fallback=True)
    assert loose.state == "resolved" and loose.selected.geography_match == "global"  # type: ignore[union-attr]
    assert any("geography fallback" in w for w in loose.warnings)  # never silent


def test_most_specific_geography_wins_and_less_specific_is_reported(tmp_path: Path) -> None:
    reg = build_registry(tmp_path, [f_record("world", value=471.0, geo="GLOBAL"), f_record("india", value=705.4)])
    res = _resolve(reg, geography_fallback=True)
    assert res.state == "resolved" and res.selected.record_id == "india"  # type: ignore[union-attr]
    assert _codes(res) == {"world": ["less_specific_geography_superseded"]}
    assert not any("geography fallback" in w for w in res.warnings)


def test_country_record_is_a_reported_fallback_for_a_subdivision(tmp_path: Path) -> None:
    reg = build_registry(tmp_path, [f_record("india")])
    assert _resolve(reg, geography="IN-AP").state == "missing"
    res = _resolve(reg, geography="in-ap", geography_fallback=True)
    assert res.state == "resolved" and res.selected.geography_match == "broader"  # type: ignore[union-attr]


def test_record_without_structured_geography_or_period_cannot_match(tmp_path: Path) -> None:
    reg = build_registry(tmp_path, [f_record("nogeo", geo=None), f_record("noper", start=None, end=None)])
    res = _resolve(reg)
    assert res.state == "missing"
    assert _codes(res) == {"nogeo": ["no_structured_geography"], "noper": ["no_structured_temporal_scope"]}


def test_malformed_request_scope_is_a_config_error(tmp_path: Path) -> None:
    reg = build_registry(tmp_path, [f_record()])
    with pytest.raises(ConfigError):
        _resolve(reg, geography="Narnia")
    with pytest.raises(ConfigError):
        _resolve(reg, period="2024-13")
    with pytest.raises(ValueError, match="latest"):
        EvidenceRequest(**request(version="latest"))


# ---------------------------------------------------------------- temporal
def test_temporal_mismatch_is_excluded(tmp_path: Path) -> None:
    res = _resolve(build_registry(tmp_path, [f_record(start="2019", end="2019")]))
    assert res.state == "missing" and _codes(res) == {"f.in.2024": ["temporal_mismatch"]}


def test_temporal_contains_versus_overlaps(tmp_path: Path) -> None:
    span = build_registry(tmp_path, [f_record(start="2020", end="2024")])
    assert _resolve(span, period="2022").selected.temporal_match == "contains"  # type: ignore[union-attr]
    assert _resolve(span, period="2023/2026").state == "missing"  # only partly covered
    loose = _resolve(span, period="2023/2026", temporal_match="overlaps")
    assert loose.state == "resolved" and loose.selected.temporal_match == "overlaps"  # type: ignore[union-attr]
    assert _resolve(span, period="2025").state == "missing"
    assert _resolve(span, period="2025", temporal_match="overlaps").state == "missing"


# ---------------------------------------------------------------- quality / status
def test_withdrawn_and_deprecated_quality(tmp_path: Path) -> None:
    reg = build_registry(tmp_path, [f_record("w", quality="withdrawn"), f_record("d", quality="deprecated")])
    res = _resolve(reg)
    assert res.state == "missing" and _codes(res) == {"w": ["quality_withdrawn"], "d": ["quality_deprecated"]}
    allowed = _resolve(reg, include_deprecated=True)
    assert allowed.state == "resolved" and allowed.selected.record_id == "d"  # type: ignore[union-attr]


def test_minimum_status_filters_weaker_origins(tmp_path: Path) -> None:
    reg = build_registry(tmp_path, [f_record()])  # sourced_unreviewed
    assert _resolve(reg, minimum_status="sourced_unreviewed").state == "resolved"
    res = _resolve(reg, minimum_status="verified_source")
    assert res.state == "missing" and _codes(res) == {"f.in.2024": ["status_below_minimum"]}


# ---------------------------------------------------------------- conflicts
def _two(tmp_path: Path, v1: float, v2: float, **kw):  # type: ignore[no-untyped-def]
    return build_registry(tmp_path, [f_record("a", value=v1, **kw), f_record("b", value=v2, **kw)])


def test_conflicting_records_are_preserved_reported_and_not_chosen(tmp_path: Path) -> None:
    reg = _two(tmp_path, 300.0, 900.0)
    res = _resolve(reg)
    assert res.state == "conflicting" and res.selected is None
    assert [r.record_id for r in res.applicable] == ["a", "b"]
    assert res.conflict is not None and res.conflict.overridden_by_caller is False
    assert res.conflict.canonical_values == pytest.approx({f"{DS}@{VER}:a": 0.3, f"{DS}@{VER}:b": 0.9})
    assert res.conflict.max_relative_spread == pytest.approx(2 / 3)
    with pytest.raises(EvidenceConflictError, match="select"):
        materialize_bindings(SPEC, {}, {"f": EvidenceRequest(**request())}, reg)


def test_conflict_applies_even_with_different_units_after_conversion(tmp_path: Path) -> None:
    reg = build_registry(tmp_path, [f_record("a", value=300.0), f_record("b", value=0.9, unit="kgCO2e/kWh")])
    res = _resolve(reg)
    assert res.state == "conflicting"  # 0.3 vs 0.9 kg/kWh, not 300 vs 0.9


def test_caller_choice_resolves_a_conflict_and_records_the_override(tmp_path: Path) -> None:
    reg = _two(tmp_path, 300.0, 900.0)
    res = _resolve(reg, select=f"{DS}@{VER}:b")
    assert res.state == "resolved" and res.selected.record_id == "b"  # type: ignore[union-attr]
    assert res.selection_rule == "caller_selected"
    assert res.conflict is not None and res.conflict.overridden_by_caller is True
    assert [r.record_id for r in res.applicable] == ["a", "b"]  # the rejected source is still preserved
    assert any("disagree" in w for w in res.warnings)


def test_overlapping_intervals_corroborate_instead_of_conflict(tmp_path: Path) -> None:
    ev = {
        "a": {"kind": "interval", "low": 280.0, "high": 340.0},
        "b": {"kind": "interval", "low": 330.0, "high": 400.0},
    }
    reg = build_registry(
        tmp_path,
        [f_record("a", value=300.0, uncertainty=ev["a"]), f_record("b", value=360.0, uncertainty=ev["b"])],
    )
    res = _resolve(reg)
    assert res.state == "resolved" and res.conflict is None
    assert res.selection_rule == AGREEING_SELECTION_RULE
    assert {r.record_id for r in res.applicable} == {"a", "b"}  # both preserved
    assert any("agree" in w for w in res.warnings)


def test_disjoint_intervals_conflict(tmp_path: Path) -> None:
    ua, ub = {"kind": "interval", "low": 280.0, "high": 310.0}, {"kind": "interval", "low": 330.0, "high": 400.0}
    reg = build_registry(
        tmp_path, [f_record("a", value=300.0, uncertainty=ua), f_record("b", value=360.0, uncertainty=ub)]
    )
    assert _resolve(reg).state == "conflicting"


def test_identical_values_from_two_records_agree_and_pick_deterministically(tmp_path: Path) -> None:
    res = _resolve(_two(tmp_path, 400.0, 400.0))
    assert res.state == "resolved" and res.selected.record_id == "a"  # type: ignore[union-attr]


# ---------------------------------------------------------------- determinism
def test_resolution_is_deterministic_and_order_independent(tmp_path: Path) -> None:
    recs = [f_record("c", value=300.0), f_record("a", value=900.0), f_record("b", value=500.0, geo="FR")]
    reg1 = build_registry(tmp_path / "one", recs)
    reg2 = build_registry(tmp_path / "two", list(reversed(recs)))
    a = _resolve(reg1).model_dump(mode="json")
    b = _resolve(reg1).model_dump(mode="json")
    c = _resolve(reg2).model_dump(mode="json")
    assert a == b  # same registry: byte-for-byte the same resolution

    def strip(d: dict) -> dict:  # type: ignore[type-arg]
        # Only the release hashes may differ: the two files hold the same records in a different order.
        txt = json.dumps(d, sort_keys=True)
        return json.loads(re.sub(r'"release_(file|content)_sha256": "[0-9a-f]{64}"', '"h": "x"', txt))

    assert strip(a) == strip(c)
    assert a["selected"] is None and a["state"] == "conflicting"  # the order of records never changes the outcome


# ---------------------------------------------------------------- binding merge rules
def test_existing_binding_that_disagrees_with_the_request_is_an_error(tmp_path: Path, e2_config) -> None:  # type: ignore[no-untyped-def]
    reg = build_registry(tmp_path, [f_record()])
    with pytest.raises(ConfigError, match="disagree"):
        resolve_requests(reg, SPEC, e2_config.bindings, {"f": EvidenceRequest(**request())})


def test_rematerializing_a_resolved_binding_is_idempotent(tmp_path: Path, e2_config) -> None:  # type: ignore[no-untyped-def]
    reg = build_registry(tmp_path, [f_record()])
    bindings = {k: v for k, v in e2_config.bindings.items() if k != "f"}
    first = materialize_bindings(SPEC, bindings, {"f": EvidenceRequest(**request())}, reg)
    again = materialize_bindings(SPEC, first.bindings, {"f": EvidenceRequest(**request())}, reg)
    assert first.bindings == again.bindings and first.verified_evidence == again.verified_evidence


def test_changed_registry_content_is_detected_on_rematerialization(tmp_path: Path, e2_config) -> None:  # type: ignore[no-untyped-def]
    bindings = {k: v for k, v in e2_config.bindings.items() if k != "f"}
    reg = build_registry(tmp_path / "v1", [f_record(value=705.4)])
    mat = materialize_bindings(SPEC, bindings, {"f": EvidenceRequest(**request())}, reg)
    changed = build_registry(tmp_path / "v2", [f_record(value=999.0)])  # same id and version, different value
    with pytest.raises(ConfigError, match="registry content has changed"):
        materialize_bindings(SPEC, mat.bindings, {"f": EvidenceRequest(**request())}, changed)


def test_requests_need_a_registry(e2_config) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ConfigError, match="registry"):
        materialize_bindings(SPEC, {}, {"f": EvidenceRequest(**request())}, None)


def test_unknown_or_non_parameter_inputs_are_config_errors(tmp_path: Path) -> None:
    reg = build_registry(tmp_path, [f_record()])
    with pytest.raises(ConfigError, match="unknown model input"):
        resolve_parameter(reg, SPEC, "not_an_input", EvidenceRequest(**request()))


def test_agreeing_records_prefer_the_strongest_origin_over_the_smaller_id(tmp_path: Path) -> None:
    from _dataset_fixtures import SYN_REVIEW

    reg = build_registry(tmp_path, [f_record("a", value=400.0), f_record("z", value=400.0, review=dict(SYN_REVIEW))])
    res = _resolve(reg)
    assert res.state == "resolved" and res.selected.record_id == "z"  # type: ignore[union-attr]
    assert res.selected.status == "verified_source"  # type: ignore[union-attr]
    assert {r.record_id for r in res.applicable} == {"a", "z"}
