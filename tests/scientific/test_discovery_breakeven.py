"""Slice C step 1: the discovery engine must reproduce analytically known break-evens.

Independent references (closed forms in verification.reference) are used only as oracles here; the
engine does not import them.
"""

from __future__ import annotations

import copy
import math

import pytest
from conftest import EXAMPLES

from ecobackfire import api
from ecobackfire.contracts.discovery import DiscoverySpec
from ecobackfire.discovery.engine import run_discovery
from ecobackfire.errors import ConfigError, EvidenceNotAvailableError, MissingEvidenceError
from ecobackfire.verification.reference import rebound_analytical as ana

pytestmark = pytest.mark.scientific
MARK = "ILLUSTRATIVE — not evidence"


def _spec(parameters: list[dict], mutate=None, **top: object) -> DiscoverySpec:  # type: ignore[type-arg,no-untyped-def]
    cfg = api.load_config(EXAMPLES / "e2_breakeven_eps.toml")
    d: dict = {  # type: ignore[type-arg]
        "discovery_id": "t",
        "title": "t",
        "research_question": "t",
        "model_ref": cfg.model_ref,
        "options": dict(cfg.options),
        "decision_metric_ref": cfg.decision_metric_ref,
        "bindings": {k: v.model_dump(mode="json") for k, v in cfg.bindings.items()},
        "parameters": parameters,
    }
    d.update(top)
    if mutate:
        mutate(d)
    return DiscoverySpec.model_validate(d)


def _rng(var: str, lo: float, hi: float, **kw: object) -> dict:  # type: ignore[type-arg]
    return {"variable_id": var, "low": lo, "high": hi, "provenance_class": "illustrative", "basis": MARK, **kw}


def _canon_inputs(spec: DiscoverySpec) -> dict[str, float]:
    return {k: float(b.value) for k, b in spec.bindings.items()}  # E2 bindings are already canonical units


def test_epsilon_breakeven_matches_closed_form() -> None:
    spec = _spec([_rng("epsilon", 0.0, 3.0, grid_points=301)])
    res = run_discovery(spec)
    assert [c.variable_id for c in res.candidates] == ["epsilon"] and not res.rejected
    c = res.candidates[0]
    want = ana.predicted_epsilon_roots(_canon_inputs(spec), spec.options)[0]
    assert math.isclose(c.value, want, rel_tol=1e-9)
    assert math.isclose(want, 1.1127574680079593, rel_tol=1e-12)  # E2 expected value (committed outputs)
    assert c.beneficial_side == "below" and c.root_kind == "crossing"
    assert c.bracket_low < c.value < c.bracket_high and (c.g_low < 0) != (c.g_high < 0)
    assert c.label == "candidate" and c.weakest_input_provenance.value == "illustrative"


def test_emissions_factor_breakeven_matches_closed_form() -> None:
    spec = _spec([_rng("f", 0.01, 1.0)])
    c = run_discovery(spec).candidates[0]
    want = ana.predicted_f_roots(_canon_inputs(spec), spec.options)[0]
    assert math.isclose(c.value, want, rel_tol=1e-9)
    assert math.isclose(want, 0.10149969274782915, rel_tol=1e-12)  # hand/reference value for E2 inputs


def test_no_crossing_is_recorded_as_rejection_with_reason() -> None:
    res = run_discovery(_spec([_rng("epsilon", 0.0, 1.0)]))  # eps* = 1.1128 lies outside [0, 1]
    assert res.candidates == []
    assert [(r.variable_id, r.code) for r in res.rejected] == [("epsilon", "no_sign_change_always_beneficial")]
    assert res.rejected[0].search_low == 0.0 and res.rejected[0].search_high == 1.0


def test_never_beneficial_range_is_rejected() -> None:
    res = run_discovery(_spec([_rng("epsilon", 1.5, 3.0)]))  # past eps*: harmful everywhere
    assert [r.code for r in res.rejected] == ["no_sign_change_never_beneficial"]


def test_unused_parameter_is_rejected_not_searched() -> None:
    res = run_discovery(_spec([{"variable_id": "s_max", "basis": "binding interval (illustrative)"}]))
    assert [r.code for r in res.rejected] == ["parameter_not_used_under_options"]


def test_invalid_region_becomes_evaluation_failed_not_a_crash() -> None:
    res = run_discovery(_spec([_rng("eta", 0.2, 1.0)]))  # eta = 1.0 violates the model's physical bound
    assert [r.code for r in res.rejected] == ["evaluation_failed"]
    assert "PhysicalConstraintError" in res.rejected[0].detail


def test_every_parameter_is_accounted_for() -> None:
    params = [
        _rng("epsilon", 0.0, 3.0),
        _rng("f", 0.01, 1.0),
        _rng("L", 8.0, 16.0),
        {"variable_id": "EM1", "basis": "binding interval (illustrative)"},
        {"variable_id": "s_max", "basis": "binding interval (illustrative)"},
    ]
    res = run_discovery(_spec(params))
    seen = {c.variable_id for c in res.candidates} | {r.variable_id for r in res.rejected}
    assert seen == {p["variable_id"] for p in params}
    assert {c.variable_id for c in res.candidates} == {"epsilon", "f"}  # matches the independent Decimal preview


def test_interval_derived_range_uses_binding_interval_in_canonical_units() -> None:
    res = run_discovery(_spec([{"variable_id": "EM1", "basis": "binding interval (illustrative)"}]))
    assert res.candidates == []
    r = res.rejected[0]
    assert (r.search_low, r.search_high) == (1000.0, 2500.0)  # E2 EM1 interval, kgCO2e


def test_score_is_transparent_and_reproducible_from_components() -> None:
    spec = _spec([_rng("epsilon", 0.0, 3.0)])
    c = run_discovery(spec).candidates[0]
    comp = c.score.components
    assert math.isclose(comp["proximity"], 1 - abs(c.value - 0.5) / 3.0, rel_tol=1e-12)
    assert comp["evidence"] == 0.0  # every input illustrative
    w = spec.weights
    assert math.isclose(c.score.total, w.proximity * comp["proximity"] + w.magnitude * comp["magnitude"], rel_tol=1e-12)
    assert all(0.0 <= v <= 1.0 for v in comp.values())


def test_ranking_orders_by_score_descending_with_stable_ids() -> None:
    res = run_discovery(_spec([_rng("epsilon", 0.0, 3.0), _rng("f", 0.01, 1.0)]))
    totals = [c.score.total for c in res.candidates]
    assert totals == sorted(totals, reverse=True)
    assert [c.rank for c in res.candidates] == [1, 2]
    assert {c.candidate_id for c in res.candidates} == {"epsilon#0", "f#0"}


def test_evidence_component_responds_to_provenance() -> None:
    def upgrade(d: dict) -> None:  # type: ignore[type-arg]
        for b in d["bindings"].values():
            b["provenance_class"] = "user_supplied"
            b["note"] = None

    c = run_discovery(_spec([_rng("epsilon", 0.0, 3.0)], mutate=upgrade)).candidates[0]
    assert math.isclose(c.score.components["evidence"], 0.2, rel_tol=1e-12)


def test_deterministic_and_input_not_mutated() -> None:
    spec = _spec([_rng("epsilon", 0.0, 3.0), _rng("f", 0.01, 1.0), _rng("L", 8.0, 16.0)])
    before = copy.deepcopy(spec.model_dump(mode="json"))
    a, b = run_discovery(spec), run_discovery(spec)
    assert a.model_dump(mode="json") == b.model_dump(mode="json")
    assert spec.model_dump(mode="json") == before
    assert len(a.spec_sha256) == 64


def test_spec_hash_changes_with_any_input() -> None:
    a = run_discovery(_spec([_rng("epsilon", 0.0, 3.0)]))
    b = run_discovery(_spec([_rng("epsilon", 0.0, 3.5)]))
    assert a.spec_sha256 != b.spec_sha256


def test_result_declares_unverified_status() -> None:
    res = run_discovery(_spec([_rng("epsilon", 0.0, 3.0)]))
    assert "unverified hypotheses" in res.status_note and res.criteria_not_yet_applied
    assert res.scoring_policy["weights"] == {"proximity": 0.4, "magnitude": 0.3, "evidence": 0.3}


def test_range_outside_model_applicability_is_warned_not_hidden() -> None:
    c = run_discovery(_spec([_rng("epsilon", 0.0, 5.0)])).candidates[0]  # model applicability for epsilon: [0, 3]
    assert any("applicability" in w and "epsilon" in w for w in c.warnings)


# ------------------------------------------------------------------ invalid / missing inputs
def test_unknown_parameter_is_config_error() -> None:
    with pytest.raises(ConfigError, match="unknown parameter"):
        run_discovery(_spec([_rng("nonsense", 0.0, 1.0)]))


def test_no_range_and_no_interval_is_config_error_never_invented() -> None:
    def drop_interval(d: dict) -> None:  # type: ignore[type-arg]
        d["bindings"]["epsilon"]["uncertainty"] = {"kind": "unknown"}

    with pytest.raises(ConfigError, match="never invents a range"):
        run_discovery(_spec([{"variable_id": "epsilon", "basis": "b"}], mutate=drop_interval))


def test_missing_required_binding_raises_missing_evidence() -> None:
    def drop(d: dict) -> None:  # type: ignore[type-arg]
        del d["bindings"]["f"]

    with pytest.raises(MissingEvidenceError):
        run_discovery(_spec([_rng("epsilon", 0.0, 3.0)], mutate=drop))


def test_sourced_binding_still_refused_until_evidence_store_is_wired() -> None:
    def sourced(d: dict) -> None:  # type: ignore[type-arg]
        d["bindings"]["f"].update(provenance_class="sourced", evidence_record_id="fixture:r1", note=None)

    with pytest.raises(EvidenceNotAvailableError):
        run_discovery(_spec([_rng("epsilon", 0.0, 3.0)], mutate=sourced))


def test_model_ref_mismatch_is_config_error() -> None:
    spec = _spec([_rng("epsilon", 0.0, 3.0)])
    from ecobackfire.models.registry import get_model

    other = copy.copy(get_model(spec.model_ref))
    object.__setattr__(other, "spec", other.spec.model_copy(update={"version": "9.9.9"}))
    with pytest.raises(ConfigError, match="does not match"):
        run_discovery(spec, model=other)
