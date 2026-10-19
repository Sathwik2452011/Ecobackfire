"""Robustness analysis (contract + one-at-a-time linspace strategy): pure numerics, no pydantic, no pytest import.

Oracles: hand-built linear fixtures whose break-even is known in closed form (g = x - 0.5 q has root 0.5 q),
the independent closed form / Decimal reference for the rebound model, and the committed E2 expected output.
No scientific data is invented; the fixtures are mathematical, not environmental claims.

Grid note: N = 98 points on [0, 1] (spacing 1/97) is used on purpose so that none of the analytic roots falls
exactly on a grid point (a root exactly on a grid point is reported by the existing scan as ``grid_zero``).
"""

import json
import math
from pathlib import Path

from ecobackfire.discovery import robustness as rb
from ecobackfire.errors import ConfigError, PhysicalConstraintError
from ecobackfire.experiments.breakeven import Root, find_breakevens
from ecobackfire.verification.reference import rebound_analytical as ana
from ecobackfire.verification.reference import rebound_reference as ref

ROOT_DIR = Path(__file__).resolve().parents[2]
N, XTOL = 98, 1e-12


def make_lin(over):  # type: ignore[no-untyped-def]
    """g(x) = x - 0.5 q - 0.001 (p - 1): root x* = 0.5 q + 0.001 (p - 1); beneficial below the root."""
    q, p = over.get("q", 1.0), over.get("p", 1.0)
    return lambda x: x - 0.5 * q - 0.001 * (p - 1.0)


BASE = {"q": 1.0, "p": 1.0}


def lin_root():  # type: ignore[no-untyped-def]
    return find_breakevens(make_lin({}), 0.0, 1.0, N, XTOL).roots[0]


def run(perts, **kw):  # type: ignore[no-untyped-def]
    return rb.analyze_robustness(make_lin, BASE, "x", lin_root(), 0.0, 1.0, grid_points=N, xtol=XTOL,
                                 perturbations=perts, **kw)


def raises_config(fn, *a, **k) -> bool:  # type: ignore[no-untyped-def]
    try:
        fn(*a, **k)
    except ConfigError:
        return True
    return False


def q_spec(**kw):  # type: ignore[no-untyped-def]
    d = dict(name="q", unit="1", baseline=1.0, low=0.9, high=1.1, n_evals=5)
    d.update(kw)
    return rb.PerturbationSpec(**d)  # type: ignore[arg-type]


def near(a, b, tol=1e-9) -> bool:  # type: ignore[no-untyped-def]
    return a is not None and abs(a - b) <= tol


# ---------------------------------------------------------------- 1. survives every configured perturbation


def test_candidate_that_survives_all_perturbations() -> None:
    rep = run([q_spec()])
    m = rep.metrics
    assert (m["n_cases"], m["n_valid"], m["n_invalid"], m["n_preserved"], m["n_lost"]) == (5, 5, 0, 5, 0)
    assert m["preservation_ratio"] == 1.0
    assert near(m["root_min"], 0.45) and near(m["root_max"], 0.55)  # analytic: 0.5 q
    assert m["baseline_reproduced"] is True and m["deterministic"] is True
    assert rep.failed_checks == []
    s = rep.parameters[0]
    assert near(s.arc_elasticity, 1.0)  # ((0.55-0.45)/0.5)/((1.1-0.9)/1)
    assert near(s.max_abs_root_shift_rel, 0.1) and s.smallest_losing_deviation is None
    assert [c.status for c in rep.cases] == ["preserved"] * 5
    assert [round(c.root, 6) for c in rep.cases] == [0.45, 0.475, 0.5, 0.525, 0.55]  # type: ignore[arg-type]
    assert rep.strategy == "oat_linspace" and rep.candidate_side == "below"


# ---------------------------------------------------------------- 2. fails under perturbation


def test_candidate_that_fails_under_perturbation() -> None:
    rep = run([q_spec(low=0.5, high=3.0, n_evals=7)])
    m = rep.metrics
    assert (m["n_valid"], m["n_preserved"], m["n_lost"]) == (7, 4, 3)  # roots 0.25,.458,.667,.875 ok; 1.08+ out
    assert near(m["preservation_ratio"], 4 / 7)
    assert [c.status for c in rep.cases] == ["preserved"] * 4 + ["lost"] * 3
    assert all(c.reason == "no_crossing_always_beneficial" for c in rep.cases if c.status == "lost")
    s = rep.parameters[0]
    assert near(s.smallest_losing_deviation, 0.5 + 4 * 2.5 / 6 - 1.0)  # first lost q = 2.1667
    assert near(s.smallest_losing_relative_deviation, s.smallest_losing_deviation)  # baseline == 1
    assert any(f.startswith("lost_under_perturbation[q]") for f in rep.failed_checks)


# ---------------------------------------------------------------- 3. dependent on a boundary


def test_candidate_close_to_the_range_edge_reports_a_small_edge_margin() -> None:
    def mk(over):  # type: ignore[no-untyped-def]
        return lambda x: x - 0.98 * over.get("q", 1.0)  # root 0.98 q, 0.02 from the upper edge

    root = find_breakevens(mk({}), 0.0, 1.0, N, XTOL).roots[0]
    rep = rb.analyze_robustness(mk, {"q": 1.0}, "x", root, 0.0, 1.0, grid_points=N, xtol=XTOL,
                                perturbations=[q_spec(low=0.95, high=1.10, n_evals=7)])
    m = rep.metrics
    assert (m["n_preserved"], m["n_lost"]) == (3, 4)  # q = 0.95, 0.975, 1.0 survive (root < 1 needs q < 1.0204)
    assert near(m["min_edge_margin"], 0.02, 1e-9)  # the root at q = 1 sits 0.02 w from the edge
    s = rep.parameters[0]
    assert near(s.smallest_losing_deviation, 0.025, 1e-9)  # q = 1.025 is the first loss


# ---------------------------------------------------------------- 4. sensitive to one parameter only


def test_sensitivity_is_attributed_to_the_parameter_that_moves_the_threshold() -> None:
    rep = run([q_spec(), rb.PerturbationSpec("p", "1", 1.0, 0.9, 1.1, n_evals=5)])
    by = {s.name: s for s in rep.parameters}
    assert [s.name for s in rep.parameters] == ["p", "q"]  # sorted by name
    assert near(by["q"].arc_elasticity, 1.0) and near(by["q"].max_abs_root_shift_rel, 0.1)
    # p: root = 0.5 + 0.001 (p - 1) -> endpoints 0.4999 / 0.5001
    assert near(by["p"].arc_elasticity, ((0.5001 - 0.4999) / 0.5) / 0.2, 1e-8)
    assert near(by["p"].max_abs_root_shift_rel, 0.0002, 1e-8)
    assert rep.metrics["dominant_parameter"] == "q"
    assert rep.metrics["preservation_ratio"] == 1.0  # both preserve the finding, yet only q moves it


# ---------------------------------------------------------------- operating point: distance, flips, effect


def test_operating_point_distance_flips_and_effect_range() -> None:
    rep = run([q_spec()], operating_point=0.52)
    m = rep.metrics
    # roots 0.45 .. 0.55; |root - 0.52| minimum is 0.005 (q = 1.05)
    assert near(m["min_boundary_distance"], 0.005, 1e-9)
    # g(0.52) = 0.52 - 0.5 q: baseline +0.02; q = 1.05, 1.1 give -0.005, -0.03 -> decision flips twice
    assert m["n_operating_flips"] == 2
    assert near(m["effect_at_op_min"], -0.03, 1e-9) and near(m["effect_at_op_max"], 0.07, 1e-9)
    assert [c.operating_flip for c in rep.cases] == [False, False, False, True, True]


# ---------------------------------------------------------------- lost for a stated reason; invalid cases


def test_lost_case_records_why_when_the_beneficial_side_reverses() -> None:
    def mk(over):  # type: ignore[no-untyped-def]
        slope = over.get("q", 1.05) - 1.0
        return lambda x: slope * (x - 0.5003)

    root = find_breakevens(mk({}), 0.0, 1.0, N, XTOL).roots[0]
    rep = rb.analyze_robustness(mk, {"q": 1.05}, "x", root, 0.0, 1.0, grid_points=N, xtol=XTOL,
                                perturbations=[q_spec(baseline=1.05, low=0.9, high=1.2, n_evals=4)])
    first = rep.cases[0]  # q = 0.9: negative slope, same location, opposite side
    assert first.status == "lost" and first.reason.startswith("roots exist but none is a converged")


def test_invalid_cases_are_listed_and_excluded_from_the_ratio() -> None:
    def mk(over):  # type: ignore[no-untyped-def]
        if over.get("q", 1.0) > 1.07:
            raise PhysicalConstraintError("q out of the model's domain")
        return lambda x: x - 0.5 * over.get("q", 1.0)

    root = find_breakevens(mk({}), 0.0, 1.0, N, XTOL).roots[0]
    rep = rb.analyze_robustness(mk, {"q": 1.0}, "x", root, 0.0, 1.0, grid_points=N, xtol=XTOL,
                                perturbations=[q_spec()])
    assert rep.metrics["n_invalid"] == 1 and rep.metrics["n_valid"] == 4
    assert rep.metrics["preservation_ratio"] == 1.0
    assert rep.cases[-1].status == "invalid" and "PhysicalConstraintError" in rep.cases[-1].reason
    assert any(f.startswith("invalid_cases[q]") for f in rep.failed_checks)


# ---------------------------------------------------------------- independent calculation


def test_independent_roots_agree_or_disagree_and_are_counted() -> None:
    def indep_ok(over):  # type: ignore[no-untyped-def]
        return [0.5 * over.get("q", 1.0) + 0.001 * (over.get("p", 1.0) - 1.0)]

    ok = run([q_spec()], reference_roots=indep_ok)
    assert ok.metrics["n_independent_compared"] == 5 and ok.metrics["n_independent_agree"] == 5
    assert ok.metrics["max_independent_dev"] < 1e-9 and ok.failed_checks == []

    bad = run([q_spec()], reference_roots=lambda over: [0.6])
    assert bad.metrics["n_independent_agree"] == 0
    assert any(f.startswith("independent_disagreement") for f in bad.failed_checks)

    none = run([q_spec()], reference_roots=lambda over: [])  # independent path finds no root: disagreement
    assert none.metrics["n_independent_agree"] == 0


# ---------------------------------------------------------------- 6. invalid perturbation configurations


def test_invalid_perturbation_specs_are_rejected() -> None:
    bad = [
        dict(low=1.1, high=0.9), dict(low=1.0, high=1.0), dict(low=1.01, high=1.1),  # order, degenerate, baseline out
        dict(n_evals=1), dict(n_evals=True), dict(n_evals=2.5), dict(n_evals=rb.MAX_EVALS_PER_PARAMETER + 1),
        dict(method="monte_carlo"), dict(basis="guess"), dict(basis="evidence_backed"),
        dict(basis="evidence_backed", evidence_ids=("a", "a")), dict(evidence_ids=("",)),
        dict(name=""), dict(name="  "), dict(unit=""), dict(low=float("nan")), dict(high=float("inf")),
        dict(baseline=float("nan")), dict(low="0.9"),
    ]
    for kw in bad:
        assert raises_config(q_spec(**kw).validate), kw
    q_spec(basis="evidence_backed", evidence_ids=("fixture:not-a-real-source",)).validate()  # a valid one passes


def test_invalid_analysis_arguments_are_rejected() -> None:
    root = lin_root()
    ok = [q_spec()]

    def go(**kw):  # type: ignore[no-untyped-def]
        args = dict(make_g=make_lin, base=BASE, search_var="x", root=root, lo=0.0, hi=1.0, grid_points=N,
                    xtol=XTOL, perturbations=ok)
        args.update(kw)
        return rb.analyze_robustness(**args)  # type: ignore[arg-type]

    go()  # the reference configuration is valid
    cases = [
        dict(perturbations=[]),
        dict(perturbations=[q_spec(), q_spec()]),  # duplicate
        dict(perturbations=[q_spec(name="x", baseline=0.5, low=0.4, high=0.6)], base={"x": 0.5}),  # searched var
        dict(perturbations=[q_spec(name="zzz")]),  # not in base
        dict(perturbations=[q_spec(baseline=0.95)]),  # baseline disagrees with base input
        dict(config=rb.RobustnessConfig(max_cases=4)),  # bounded search
        dict(config=rb.RobustnessConfig(repeats=1)), dict(config=rb.RobustnessConfig(agree_tol=0.0)),
        dict(lo=1.0, hi=0.0), dict(grid_points=2), dict(xtol=0.0), dict(operating_point=2.0),
        dict(root=Root(5.0, "crossing", None, None, None, None, 0.0, 0, True, "below")),  # outside the range
        dict(root=Root(0.5, "crossing", None, None, None, None, 0.0, 0, True, "unknown")),  # no side
    ]
    for kw in cases:
        assert raises_config(go, **kw), kw


def test_relative_spec_rejects_empty_or_nonsensical_ranges() -> None:
    assert raises_config(rb.relative_spec, "q", "1", 0.0, 0.1)
    assert raises_config(rb.relative_spec, "q", "1", 1.0, 1.0)
    assert raises_config(rb.relative_spec, "q", "1", 1.0, 0.0)
    s = rb.relative_spec("q", "1", -2.0, 0.1, 3)  # negative baseline: bounds are ordered
    assert (s.low, s.high) == (-2.2, -1.8) and s.values()[1] == -2.0 and s.basis == "configured"


# ---------------------------------------------------------------- 7. unit mismatches


def test_unit_mismatch_is_refused_before_any_evaluation() -> None:
    calls = []

    def mk(over):  # type: ignore[no-untyped-def]
        calls.append(1)
        return make_lin(over)

    root = lin_root()

    def go(units):  # type: ignore[no-untyped-def]
        return rb.analyze_robustness(mk, BASE, "x", root, 0.0, 1.0, grid_points=N, xtol=XTOL,
                                     perturbations=[q_spec(unit="g")], expected_units=units)

    assert raises_config(go, {"q": "kg"})  # mismatch
    assert raises_config(go, {"p": "kg"})  # no expected unit for q: cannot check
    assert calls == []  # nothing evaluated
    assert go({"q": "g"}).metrics["n_preserved"] == 5


# ---------------------------------------------------------------- labels: configured experiment vs ranged analysis


def test_analysis_is_labelled_configured_unless_every_range_is_backed() -> None:
    cfg = run([q_spec()])
    assert cfg.analysis_kind == "configured_sensitivity_experiment" and "CONFIGURED SENSITIVITY" in cfg.analysis_label
    mixed = run([q_spec(basis="user_uncertainty"), rb.PerturbationSpec("p", "1", 1.0, 0.9, 1.1)])
    assert mixed.analysis_kind == "configured_sensitivity_experiment"  # one configured range is enough
    backed = run([q_spec(basis="evidence_backed", evidence_ids=("fixture:not-a-real-source",)),
                  rb.PerturbationSpec("p", "1", 1.0, 0.9, 1.1, basis="user_uncertainty")])
    assert backed.analysis_kind == "user_or_evidence_ranged_sensitivity"
    assert "not a probabilistic" in backed.analysis_label
    assert "not probabilities" in backed.interpretation


# ---------------------------------------------------------------- 8. deterministic repeated execution


def test_identical_inputs_give_identical_reports_and_changes_change_the_hash() -> None:
    a, b = run([q_spec()]), run([q_spec()])
    assert a.sha256() == b.sha256() and a.to_dict() == b.to_dict()
    p = rb.PerturbationSpec("p", "1", 1.0, 0.9, 1.1)
    assert run([q_spec(), p]).sha256() == run([p, q_spec()]).sha256()  # order of the list is irrelevant
    assert run([q_spec(n_evals=6)]).sha256() != a.sha256()
    assert run([q_spec(high=1.2)]).sha256() != a.sha256()
    assert run([q_spec()], config=rb.RobustnessConfig(agree_tol=1e-6)).sha256() != a.sha256()
    json.dumps(a.to_dict(), allow_nan=False)  # no inf / NaN


def test_a_non_deterministic_model_is_detected() -> None:
    state = {"k": 0}

    def mk(over):  # type: ignore[no-untyped-def]
        state["k"] += 1
        shift = 1e-3 * state["k"]
        return lambda x: x - 0.5 * over.get("q", 1.0) - shift

    root = find_breakevens(mk({}), 0.0, 1.0, N, XTOL).roots[0]
    rep = rb.analyze_robustness(mk, {"q": 1.0}, "x", root, 0.0, 1.0, grid_points=N, xtol=XTOL,
                                perturbations=[q_spec(n_evals=2)])
    assert rep.metrics["deterministic"] is False
    assert any(f.startswith("nondeterministic") for f in rep.failed_checks)
    assert any(f.startswith("baseline_not_reproduced") for f in rep.failed_checks)


def test_baseline_that_no_longer_reproduces_the_candidate_is_a_failed_check() -> None:
    wrong = Root(0.4, "crossing", 0.39, 0.41, -0.01, 0.01, 0.0, 3, True, "below")  # model root is 0.5
    rep = rb.analyze_robustness(make_lin, BASE, "x", wrong, 0.0, 1.0, grid_points=N, xtol=XTOL,
                                perturbations=[q_spec()])
    assert rep.metrics["baseline_reproduced"] is False
    assert rep.failed_checks[0].startswith("baseline_not_reproduced")


# ---------------------------------------------------------------- 10. regression: existing E2 experiment


E2 = dict(E0=3000.0, eta=0.30, phi=0.80, epsilon=0.5, f=0.40, EM0=1000.0, EM1=1720.0, L=12.0, s_max=1.5)
OPTS = dict(boundary_variant="B1", demand_form="constant_elasticity")


def e2_make_g(over):  # type: ignore[no-untyped-def]
    inputs = {**E2, **over}
    return lambda eps: float(ref.evaluate({**inputs, "epsilon": eps}, OPTS)["dC"])


def e2_closed_form(over):  # type: ignore[no-untyped-def]
    inputs = {**E2, **over}
    r = ana.embodied_burden_r(inputs, "B1")
    root = ana.eps_star(inputs["eta"], inputs["phi"], r)
    return [] if root is None else [root]


def test_e2_experiment_under_configured_perturbations_matches_the_closed_form_in_every_case() -> None:
    n = 301
    g = e2_make_g({})
    root = find_breakevens(g, 0.0, 3.0, n, XTOL).roots[0]
    committed = json.loads(
        (ROOT_DIR / "examples/rebound_breakeven/expected/e2_breakeven_eps/result.json").read_text()
    )["breakeven"]["roots"][0]["value"]
    assert math.isclose(root.value, committed, abs_tol=1e-12)  # same candidate as the committed E2 baseline
    specs = [rb.relative_spec(k, u, E2[k], 0.05, 5)
             for k, u in (("eta", "1"), ("phi", "1"), ("EM1", "MJ"))]
    rep = rb.analyze_robustness(e2_make_g, E2, "epsilon", root, 0.0, 3.0, grid_points=n, xtol=XTOL,
                                perturbations=specs, reference_roots=e2_closed_form, operating_point=0.5,
                                expected_units={"eta": "1", "phi": "1", "EM1": "MJ"}, subject="epsilon#0")
    m = rep.metrics
    assert (m["n_cases"], m["n_preserved"], m["preservation_ratio"]) == (15, 15, 1.0)
    assert m["baseline_reproduced"] and m["deterministic"] and rep.failed_checks == []
    assert m["n_independent_compared"] == 15 and m["n_independent_agree"] == 15
    for c in rep.cases:  # every perturbed root equals the closed form of the perturbed inputs
        assert math.isclose(c.root, e2_closed_form({c.parameter: c.value})[0], rel_tol=1e-9)  # type: ignore[arg-type]
    by = {s.name: s for s in rep.parameters}
    # analytic sign of the effect of each input on eps*: eps* falls with phi and eta*phi... only assert phi
    # (strictly decreasing) and EM1 (strictly decreasing: more embodied burden lowers the break-even)
    assert by["phi"].arc_elasticity < 0 and by["EM1"].arc_elasticity < 0
    assert m["dominant_parameter"] in {"eta", "phi", "EM1"}
    assert rep.analysis_kind == "configured_sensitivity_experiment"
    assert m["n_operating_flips"] == 0  # eps_op = 0.5 stays below every perturbed eps* (~1.1)


# ---------------------------------------------------------------- normalisation does not depend on baseline == 1


def test_elasticity_and_relative_deviation_are_normalised_by_a_non_unit_baseline() -> None:
    def mk(over):  # type: ignore[no-untyped-def]
        return lambda x: x - 0.1 * over.get("q", 4.0)  # root 0.1 q; q* = 0.4 at baseline q = 4

    root = find_breakevens(mk({}), 0.0, 1.0, N, XTOL).roots[0]
    spec = rb.PerturbationSpec("q", "1", 4.0, 3.6, 4.4, n_evals=5)
    rep = rb.analyze_robustness(mk, {"q": 4.0}, "x", root, 0.0, 1.0, grid_points=N, xtol=XTOL, perturbations=[spec])
    s = rep.parameters[0]
    assert near(s.arc_elasticity, 1.0)  # ((0.44 - 0.36)/0.4) / ((4.4 - 3.6)/4)
    assert near(rep.cases[0].relative_deviation, -0.1) and near(rep.cases[0].deviation, -0.4)
    assert near(rep.cases[0].root_shift_rel, -0.1) and near(rep.cases[4].root_shift_rel, 0.1)
    wide = rb.PerturbationSpec("q", "1", 4.0, 3.6, 12.0, n_evals=5)  # q = 12 -> root 1.2: lost beyond q = 10
    lost = rb.analyze_robustness(mk, {"q": 4.0}, "x", root, 0.0, 1.0, grid_points=N, xtol=XTOL, perturbations=[wide])
    p = lost.parameters[0]
    assert near(p.smallest_losing_deviation, 12.0 - 4.0) and near(p.smallest_losing_relative_deviation, 2.0)


def test_a_crossing_with_the_opposite_beneficial_side_does_not_preserve_the_candidate() -> None:
    def mk(over):  # type: ignore[no-untyped-def]
        sign = -1.0 if over.get("q", 1.0) < 0.95 else 1.0  # same root 0.5003, beneficial side flips
        return lambda x: sign * (x - 0.5003)

    root = find_breakevens(mk({}), 0.0, 1.0, N, XTOL).roots[0]
    rep = rb.analyze_robustness(mk, {"q": 1.0}, "x", root, 0.0, 1.0, grid_points=N, xtol=XTOL,
                                perturbations=[q_spec(low=0.9, high=1.1, n_evals=3)])
    assert [c.status for c in rep.cases] == ["lost", "preserved", "preserved"]
    assert rep.cases[0].n_roots == 1 and rep.cases[0].reason.startswith("roots exist but none is")
