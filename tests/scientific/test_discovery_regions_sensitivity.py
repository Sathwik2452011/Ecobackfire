"""Slice C step 2: 2-D reversal regions and seeded sensitivity, through the full engine.

Oracles: closed-form eps*(eta, phi, r) from the independent reference, and the pure sampling module driven
by that closed form (same QMC stream, so engine and oracle must agree).
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from conftest import EXAMPLES

from ecobackfire import api
from ecobackfire.contracts.discovery import DiscoveryResult, DiscoverySpec
from ecobackfire.discovery import sampling as sp
from ecobackfire.discovery.engine import run_discovery
from ecobackfire.discovery.loader import load_discovery_spec
from ecobackfire.errors import ConfigError
from ecobackfire.verification.reference import rebound_analytical as ana

pytestmark = pytest.mark.scientific
MARK = "ILLUSTRATIVE — not evidence"
D2 = EXAMPLES.parent / "discovery" / "d2_regions_sensitivity.toml"
NAMES = ["phi", "EM1", "EM0", "f", "L", "E0", "eta"]


def _ax(var: str, lo: float | None = None, hi: float | None = None, n: int = 21) -> dict:  # type: ignore[type-arg]
    basis = MARK if lo is not None else "binding interval (illustrative)"
    d: dict = {"variable_id": var, "grid_points": n, "basis": basis}  # type: ignore[type-arg]
    if lo is not None:
        d.update(low=lo, high=hi, provenance_class="illustrative")
    return d


def _spec(regions=(), sensitivity=None, parameters=()) -> DiscoverySpec:  # type: ignore[no-untyped-def]
    cfg = api.load_config(EXAMPLES / "e2_breakeven_eps.toml")
    return DiscoverySpec.model_validate(
        {
            "discovery_id": "t2",
            "title": "t",
            "research_question": "t",
            "model_ref": cfg.model_ref,
            "options": dict(cfg.options),
            "decision_metric_ref": cfg.decision_metric_ref,
            "bindings": {k: v.model_dump(mode="json") for k, v in cfg.bindings.items()},
            "parameters": list(parameters),
            "regions": [{"x": x, "y": y} for x, y in regions],
            "sensitivity": sensitivity,
        }
    )


def _inputs(spec: DiscoverySpec) -> dict[str, float]:
    return {k: float(b.value) for k, b in spec.bindings.items()}


def _sens(seed: int = 12345, log2: int = 8, params: list[str] | None = None) -> dict:  # type: ignore[type-arg]
    return {
        "seed": seed,
        "n_samples_log2": log2,
        "target": _ax("epsilon", 0.0, 3.0, n=101),
        "parameters": [{"variable_id": v, "basis": "binding interval (illustrative)"} for v in (params or NAMES)],
    }


# ------------------------------------------------------------------ regions
def test_region_boundary_matches_closed_form_and_counts() -> None:
    spec = _spec([(_ax("phi", 0.5, 1.0, 21), _ax("epsilon", 0.0, 3.0, 41))])
    res = run_discovery(spec)
    assert len(res.regions) == 1 and not res.rejected
    rg = res.regions[0]
    r = ana.embodied_burden_r(_inputs(spec), "B1")
    eta = _inputs(spec)["eta"]
    assert len(rg.boundary_points) == 35
    for phi, eps in rg.boundary_points:
        assert math.isclose(ana.eps_star(eta, phi, r), eps, rel_tol=1e-9)
    assert (rg.n_beneficial, rg.n_not_beneficial, rg.n_zero) == (361, 500, 0)  # same grid as the pure-module run
    assert math.isclose(rg.fraction_not_beneficial, 500 / 861)
    assert rg.base_decision == "beneficial" and rg.base_inside_ranges
    assert abs(rg.base_to_boundary_distance - 0.19774170898553264) < 1e-6
    assert rg.label == "candidate" and "empirical validation" in rg.hypothesis


def test_region_without_reversal_is_a_recorded_rejection() -> None:
    lo = run_discovery(_spec([(_ax("phi", 0.5, 1.0, 11), _ax("epsilon", 0.0, 0.8, 11))]))
    assert lo.regions == [] and [(r.variable_id, r.code) for r in lo.rejected] == [
        ("phi|epsilon", "no_reversal_in_region_always_beneficial")
    ]
    hi = run_discovery(_spec([(_ax("phi", 0.5, 1.0, 11), _ax("epsilon", 2.0, 3.0, 11))]))
    assert [r.code for r in hi.rejected] == ["no_reversal_in_region_never_beneficial"]


def test_region_unused_parameter_and_invalid_domain_are_rejections_not_crashes() -> None:
    res = run_discovery(_spec([(_ax("phi", 0.5, 1.0, 5), _ax("s_max", 0.5, 2.0, 5))]))
    assert [r.code for r in res.rejected] == ["parameter_not_used_under_options"]
    res = run_discovery(_spec([(_ax("epsilon", 0.0, 3.0, 5), _ax("eta", 0.2, 1.0, 5))]))  # eta = 1 is unphysical
    assert [r.code for r in res.rejected] == ["evaluation_failed"]
    assert "PhysicalConstraintError" in res.rejected[0].detail


def test_unknown_variable_in_region_or_sensitivity_is_config_error() -> None:
    with pytest.raises(ConfigError, match="unknown parameter"):
        run_discovery(_spec([(_ax("phi", 0.5, 1.0, 5), _ax("nonsense", 0.0, 1.0, 5))]))
    bad = _sens()
    bad["parameters"][0]["variable_id"] = "nonsense"
    with pytest.raises(ConfigError, match="unknown parameter"):
        run_discovery(_spec(sensitivity=bad))


# ------------------------------------------------------------------ sensitivity
def _oracle(spec: DiscoverySpec, seed: int, log2: int):  # type: ignore[no-untyped-def]
    base = _inputs(spec)
    lows = [spec.bindings[n].uncertainty.low for n in NAMES]  # type: ignore[union-attr]
    highs = [spec.bindings[n].uncertainty.high for n in NAMES]  # type: ignore[union-attr]

    def target(p: np.ndarray) -> float | None:
        i = {**base, **dict(zip(NAMES, map(float, p), strict=True))}
        v = ana.eps_star(i["eta"], i["phi"], ana.embodied_burden_r(i, "B1"))
        return v if v is not None and 0.0 <= v <= 3.0 else None  # engine searches epsilon in [0, 3]

    return sp.sample_function(target, lows, highs, log2, seed)


def test_sensitivity_matches_closed_form_oracle_on_the_same_qmc_stream() -> None:
    spec = _spec(sensitivity=_sens())
    s = run_discovery(spec).sensitivity
    assert s is not None
    ora = _oracle(spec, 12345, 8)
    want = dict(zip(NAMES, sp.rank_correlations(ora.points, ora.values), strict=True))
    got = {r.variable_id: r.rank_correlation for r in s.rows}
    for n in NAMES:
        assert (want[n] is None) == (got[n] is None)
        if want[n] is not None:
            assert abs(want[n] - got[n]) < 1e-6
    assert s.n_samples == 256 and s.n_failed == 0 and s.n_with_breakeven == ora.n_defined


def test_sensitivity_ranking_and_analytic_signs() -> None:
    s = run_discovery(_spec(sensitivity=_sens())).sensitivity
    assert s is not None
    by = {r.variable_id: r for r in s.rows}
    assert s.rows[0].variable_id == "phi" and s.rows[0].rank == 1
    assert by["phi"].rank_correlation < -0.6 and by["EM1"].rank_correlation < -0.2 and by["EM0"].rank_correlation > 0.1
    ranks = [r.rank for r in s.rows]
    assert ranks == list(range(1, len(ranks) + 1))
    absvals = [r.abs_rank_correlation for r in s.rows]
    assert absvals == sorted(absvals, reverse=True)
    assert 0.0 < s.fraction_with_breakeven < 1.0 and s.target_quantiles["min"] <= s.target_quantiles["max"]
    assert s.limitations and s.scipy_version and "screening" in s.method


def test_sensitivity_is_deterministic_and_seed_dependent() -> None:
    a = run_discovery(_spec(sensitivity=_sens(seed=7))).model_dump(mode="json")
    b = run_discovery(_spec(sensitivity=_sens(seed=7))).model_dump(mode="json")
    c = run_discovery(_spec(sensitivity=_sens(seed=8))).model_dump(mode="json")
    assert a == b
    assert a["sensitivity"]["target_quantiles"] != c["sensitivity"]["target_quantiles"]
    assert a["sensitivity"]["seed"] == 7 and c["sensitivity"]["seed"] == 8
    assert a["spec_sha256"] != c["spec_sha256"]


def test_sensitivity_skips_unused_inputs_and_rejects_bad_setups() -> None:
    s = run_discovery(_spec(sensitivity=_sens(params=["phi", "s_max"]))).sensitivity
    assert s is not None and [r.variable_id for r in s.rows] == ["phi"]
    assert len(s.skipped) == 1 and "s_max" in s.skipped[0]
    with pytest.raises(ConfigError, match="no sampled parameter"):
        run_discovery(_spec(sensitivity=_sens(params=["s_max"])))
    bad = _sens()
    bad["target"] = _ax("s_max", 0.5, 2.0)
    with pytest.raises(ConfigError, match="not a required input"):
        run_discovery(_spec(sensitivity=bad))


# ------------------------------------------------------------------ end to end on the shipped example
def test_d2_example_end_to_end_and_roundtrip() -> None:
    res = run_discovery(load_discovery_spec(D2))
    assert [(g.x_variable_id, g.y_variable_id) for g in res.regions] == [("phi", "epsilon")]
    assert [(r.variable_id, r.code) for r in res.rejected] == [("eta|EM1", "no_reversal_in_region_always_beneficial")]
    assert res.sensitivity is not None and res.sensitivity.seed == 12345
    assert res.sensitivity.rows[0].variable_id == "phi"
    assert res.weakest_input_provenance.value == "illustrative" and "unverified hypotheses" in res.status_note
    again = DiscoveryResult.model_validate(res.model_dump(mode="json"))
    assert again.model_dump(mode="json") == res.model_dump(mode="json")
    assert res.model_dump(mode="json") == run_discovery(load_discovery_spec(D2)).model_dump(mode="json")
