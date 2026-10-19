"""Falsification checks on 1-D break-even candidates: pure numerics, no pydantic, no pytest import
(runnable anywhere numpy/scipy exist).

Oracles: hand-built functions with known behaviour, the independent closed form and Decimal reference
(verification.reference), and the committed E2 expected result.  Every test asserts a scientific property
(a candidate fails / survives / is inconclusive for a stated reason), not just that an object is returned.
"""

import itertools
import json
import math
import random
from pathlib import Path

from ecobackfire.discovery import falsification as fz
from ecobackfire.errors import ConfigError, PhysicalConstraintError
from ecobackfire.experiments.breakeven import Root, find_breakevens
from ecobackfire.verification.reference import rebound_analytical as ana
from ecobackfire.verification.reference import rebound_reference as ref

ROOT_DIR = Path(__file__).resolve().parents[2]
E2 = dict(E0=3000.0, eta=0.30, phi=0.80, epsilon=0.5, f=0.40, EM0=1000.0, EM1=1720.0, L=12.0, s_max=1.5)
OPTS = dict(boundary_variant="B1", demand_form="constant_elasticity")
N, XTOL, LO, HI = 301, 1e-12, 0.0, 3.0
PERTURB = ["E0", "eta", "phi", "f", "EM0", "EM1", "L"]


def e2_make_g(over):  # type: ignore[no-untyped-def]
    inputs = {**E2, **over}
    return lambda eps: float(ref.evaluate({**inputs, "epsilon": eps}, OPTS)["dC"])


def e2_root():  # type: ignore[no-untyped-def]
    g = e2_make_g({})
    return g, find_breakevens(g, LO, HI, N, XTOL).roots[0]


def status(rep, prefix):  # type: ignore[no-untyped-def]
    return next(c for c in rep.checks if c.check_id.startswith(prefix))


def raises_config(fn, *a, **k) -> bool:  # type: ignore[no-untyped-def]
    try:
        fn(*a, **k)
    except ConfigError:
        return True
    return False


def run(g, lo, hi, n=201, root=None, **kw):  # type: ignore[no-untyped-def]
    root = root or find_breakevens(g, lo, hi, n, 1e-12).roots[0]
    return fz.falsify_breakeven(g, root, lo, hi, grid_points=n, xtol=1e-12, **kw)


# ---------------------------------------------------------------- regression against the existing E2 experiment


def test_e2_root_matches_committed_expected_output_and_closed_form() -> None:
    expected = json.loads((ROOT_DIR / "examples/rebound_breakeven/expected/e2_breakeven_eps/result.json").read_text())
    committed = expected["breakeven"]["roots"][0]["value"]
    _, root = e2_root()
    assert math.isclose(root.value, committed, abs_tol=1e-12)
    r = ana.embodied_burden_r(E2, "B1")
    assert math.isclose(root.value, ana.eps_star(E2["eta"], E2["phi"], r), rel_tol=1e-9)


def test_e2_survives_every_attempt_when_independent_roots_are_supplied() -> None:
    g, root = e2_root()
    dec = [float(x) for x in ref.breakevens(E2, OPTS, "epsilon", LO, HI, N)]
    cf = ana.eps_star(E2["eta"], E2["phi"], ana.embodied_burden_r(E2, "B1"))
    for refs in ([cf], dec):
        rep = fz.falsify_breakeven(g, root, LO, HI, grid_points=N, xtol=XTOL, subject="epsilon#0",
                                   make_g=e2_make_g, base=E2, search_var="epsilon", perturb_vars=PERTURB,
                                   reference_roots=refs, domain=(0.0, math.inf))
        assert [c.status for c in rep.checks] == ["passed"] * 6, [(c.check_id, c.status, c.summary) for c in rep.checks]
        assert not rep.falsified and not rep.limits and not rep.not_assessed


def test_e2_sensitivity_matches_the_analytic_log_derivative() -> None:
    g, root = e2_root()
    rep = fz.falsify_breakeven(g, root, LO, HI, grid_points=N, xtol=XTOL, make_g=e2_make_g, base=E2,
                               search_var="epsilon", perturb_vars=PERTURB)
    m = status(rep, "X5").metrics
    assert all(m[f"preserved_fraction@{L:g}"] == 1.0 for L in (0.01, 0.05, 0.10))

    def eps_of(v: str, factor: float) -> float:
        i = {**E2, v: E2[v] * factor}
        return ana.eps_star(i["eta"], i["phi"], ana.embodied_burden_r(i, "B1"))  # type: ignore[return-value]

    for v in PERTURB:
        h = 1e-6  # independent, much smaller step than the check's 1% level
        truth = (math.log(eps_of(v, 1 + h)) - math.log(eps_of(v, 1 - h))) / (math.log(1 + h) - math.log(1 - h))
        assert math.isclose(m[f"S[{v}]"], truth, rel_tol=0.02, abs_tol=2e-3), (v, m[f"S[{v}]"], truth)
    truths = {v: abs((math.log(eps_of(v, 1.001)) - math.log(eps_of(v, 0.999))) / 0.002) for v in PERTURB}
    assert m["dominant_input"] == f"S[{max(truths, key=lambda k: truths[k])}]"


# ---------------------------------------------------------------- candidates that fail or are limited by perturbation


def _fragile(p: float):  # type: ignore[no-untyped-def]
    def make(over):  # type: ignore[no-untyped-def]
        q = over.get("p", p)
        return lambda x: (x - 0.5) ** 2 - (q - 1.0)  # two roots exist only if q >= 1

    return make


def test_candidate_lost_under_one_percent_perturbation_fails_but_stays_numerically_sound() -> None:
    make = _fragile(1.004)
    g = make({})
    scan = find_breakevens(g, 0.0, 1.0, 201, 1e-12)
    assert len(scan.roots) == 2
    for root in scan.roots:
        rep = fz.falsify_breakeven(g, root, 0.0, 1.0, grid_points=201, xtol=1e-12, make_g=make,
                                   base={"p": 1.004}, search_var="x", perturb_vars=["p"])
        x5 = status(rep, "X5")
        assert x5.status == "failed" and x5.category == "perturbation_fragile"
        assert x5.metrics["preserved_fraction@0.01"] == 0.5  # +1% keeps the roots, -1% (p < 1) removes them
        assert x5.metrics["lost"] == "p-0.01,p-0.05,p-0.1"
        assert rep.falsified and [c.check_id[:2] for c in rep.failed] == ["X5"]
        for p in ("X1", "X2", "X3", "X4"):  # the arithmetic is fine; the finding is what is fragile
            assert status(rep, p).status == "passed", p


def test_candidate_lost_only_at_larger_perturbation_limits_the_claim() -> None:
    make = _fragile(1.06)  # -1% -> 1.0494, -5% -> 1.007 (still roots), -10% -> 0.954 (none)
    root = find_breakevens(make({}), 0.0, 1.0, 201, 1e-12).roots[0]
    rep = fz.falsify_breakeven(make({}), root, 0.0, 1.0, grid_points=201, xtol=1e-12, make_g=make,
                               base={"p": 1.06}, search_var="x", perturb_vars=["p"])
    x5 = status(rep, "X5")
    assert x5.status == "limits_claim"
    assert x5.metrics["preserved_fraction@0.01"] == 1.0 and x5.metrics["preserved_fraction@0.1"] == 0.5
    assert not rep.falsified and rep.limits == [x5]


# ---------------------------------------------------------------- boundary dependence


def test_root_next_to_the_configured_edge_is_boundary_dependent() -> None:
    rep = run(lambda x: x - 0.001, 0.0, 1.0)
    x3 = status(rep, "X3")
    assert x3.status == "failed" and x3.category == "boundary_dependence"
    assert math.isclose(x3.metrics["margin_over_w"], 0.001, rel_tol=1e-6)
    assert status(rep, "X1").status == "passed"  # a clean crossing, merely at the edge


def test_interior_root_passes_and_expanded_range_with_extra_root_limits_the_claim() -> None:
    assert status(run(lambda x: x - 0.5, 0.0, 1.0), "X3").status == "passed"
    clipped = status(run(lambda x: x - 0.5, 0.0, 1.0, domain=(0.0, 1.0)), "X3")
    assert clipped.status == "passed" and "cannot be expanded" in clipped.summary
    # second root at 1.1 lies just outside [0, 1]: the single-root reading is range-dependent
    x3 = status(run(lambda x: (x - 0.5) * (x - 1.1), 0.0, 1.0), "X3")
    assert x3.status == "limits_claim" and x3.metrics["n_new_roots_outside_range"] == 1


# ---------------------------------------------------------------- numerical instability


def test_triple_root_has_a_real_sign_change_but_fails_numerical_stability() -> None:
    g = lambda x: (x - 1.0) ** 3  # noqa: E731
    rep = run(g, 0.0, 2.2)
    assert status(rep, "X1").status == "passed"  # the sign change is genuine ...
    x2 = status(rep, "X2")
    assert x2.status == "failed" and x2.category == "numerical_instability"
    # ... the three solvers agree, so the failure is conditioning, not disagreement ...
    assert x2.metrics["max_path_deviation_over_w"] < 1e-10 and x2.metrics["paths_without_root"] == ""
    # ... and a 1e-10-relative change of g moves a triple root by (1e-10 * G)^(1/3) / w, G = max|g| at the ends
    expected = (1e-10 * max(1.0, 1.2**3)) ** (1.0 / 3.0) / 2.2
    assert math.isclose(x2.metrics["noise_shift_over_w"], expected, rel_tol=0.01)


def test_well_conditioned_linear_root_passes_numerical_stability() -> None:
    x2 = status(run(lambda x: x - 1.0, 0.0, 2.2), "X2")
    assert x2.status == "passed" and x2.metrics["noise_shift_over_w"] < 1e-9


def test_a_wrong_bracket_is_reported_without_nan_and_still_hashable() -> None:
    g = lambda x: x - 0.5  # noqa: E731
    bad = Root(0.5, "crossing", 0.6, 0.9, 0.1, 0.4, 0.0, 1, True, "below")  # bracket does not contain the root
    rep = fz.falsify_breakeven(g, bad, 0.0, 1.0, grid_points=201, xtol=1e-12)
    x2 = status(rep, "X2")
    assert x2.status == "failed" and x2.metrics["paths_without_root"] == "bisection"
    assert len(rep.sha256()) == 64  # inf was mapped to None, so canonical JSON (allow_nan=False) works


# ---------------------------------------------------------------- false crossings


def test_touching_root_is_a_false_crossing_and_is_not_promoted_by_other_checks() -> None:
    g = lambda x: (x - 0.5) ** 2  # noqa: E731
    scan = find_breakevens(g, 0.0, 1.0, 201, 1e-12)
    assert [r.kind for r in scan.roots] == ["grid_zero"]  # the existing scan reports the touch as a root
    rep = fz.falsify_breakeven(g, scan.roots[0], 0.0, 1.0, grid_points=201, xtol=1e-12)
    assert status(rep, "X1").status == "failed" and status(rep, "X1").category == "false_crossing"
    assert status(rep, "X2").status == "inconclusive"  # no sign-changing bracket: never silently 'passed'
    assert rep.falsified


def test_jump_discontinuity_is_a_false_crossing() -> None:
    g = lambda x: -1.0 if x < 0.5 else 1.0  # noqa: E731
    scan = find_breakevens(g, 0.0, 1.0, 201, 1e-12)
    assert scan.roots[0].kind == "discontinuity_suspected"
    x1 = status(fz.falsify_breakeven(g, scan.roots[0], 0.0, 1.0, grid_points=201, xtol=1e-12), "X1")
    assert x1.status == "failed" and x1.metrics["continuity_ratio"] > 0.9


def test_claimed_direction_contradicting_the_sign_pattern_fails() -> None:
    g = lambda x: x - 0.5  # noqa: E731
    real = find_breakevens(g, 0.0, 1.0, 201, 1e-12).roots[0]
    assert real.beneficial_side == "below"
    lie = Root(real.value, "crossing", real.bracket_low, real.bracket_high, real.g_low, real.g_high,
               real.residual, real.iterations, True, "above")
    x1 = status(fz.falsify_breakeven(g, lie, 0.0, 1.0, grid_points=201, xtol=1e-12), "X1")
    assert x1.status == "failed" and x1.metrics["direction_matches_claim"] is False


def test_evaluation_failure_next_to_the_root_fails_the_candidate() -> None:
    def g(x: float) -> float:
        if x > 0.505:
            raise PhysicalConstraintError("outside the model's physical domain")
        return x - 0.5

    root = Root(0.5, "crossing", 0.495, 0.5, -0.005, 0.0, 0.0, 1, True, "below")
    x1 = status(fz.falsify_breakeven(g, root, 0.0, 1.0, grid_points=201, xtol=1e-12), "X1")
    assert x1.status == "failed" and "PhysicalConstraintError" in x1.summary


# ---------------------------------------------------------------- no-crossing verdicts


def test_genuine_no_crossing_survives_a_dense_rescan() -> None:
    rep = fz.falsify_no_crossing(lambda x: x * x + 1.0, 0.0, 1.0, grid_points=201, xtol=1e-12)
    assert [c.status for c in rep.checks] == ["passed"] and rep.root is None


def test_sub_grid_double_root_falsifies_a_no_crossing_verdict() -> None:
    g = lambda x: (x - 0.3012) * (x - 0.3016)  # noqa: E731  # both roots inside one 0.005-wide grid cell
    assert find_breakevens(g, 0.0, 1.0, 201, 1e-12).classification == "no_crossing_never_beneficial"
    chk = fz.falsify_no_crossing(g, 0.0, 1.0, grid_points=201, xtol=1e-12).checks[0]
    assert chk.status == "failed" and chk.category == "false_no_crossing"


def test_near_touching_minimum_leaves_no_crossing_inconclusive() -> None:
    g = lambda x: (x - 0.3012) ** 2  # noqa: E731  # touches zero between grid points; never changes sign
    chk = fz.falsify_no_crossing(g, 0.0, 1.0, grid_points=201, xtol=1e-12).checks[0]
    assert chk.status == "inconclusive" and chk.metrics["min_abs_g_rel"] < 1e-6


# ---------------------------------------------------------------- determinism and reproducibility


def test_repeated_execution_is_identical_down_to_the_report_hash() -> None:
    g, root = e2_root()
    kw = dict(grid_points=N, xtol=XTOL, make_g=e2_make_g, base=E2, search_var="epsilon", perturb_vars=PERTURB)
    a = fz.falsify_breakeven(g, root, LO, HI, **kw)  # type: ignore[arg-type]
    b = fz.falsify_breakeven(g, root, LO, HI, **kw)  # type: ignore[arg-type]
    assert a == b and a.sha256() == b.sha256()
    cfg = fz.FalsificationConfig(perturbation_levels=(0.02, 0.04))
    c = fz.falsify_breakeven(g, root, LO, HI, config=cfg, **kw)  # type: ignore[arg-type]
    assert c.sha256() != a.sha256() and c.config["perturbation_levels"] == (0.02, 0.04)


def test_report_records_every_threshold_that_influenced_it() -> None:
    rep = run(lambda x: x - 0.5, 0.0, 1.0)
    assert set(rep.config) == set(fz.FalsificationConfig.__dataclass_fields__)
    assert set(rep.metric_definitions) == {"X1", "X2", "X3", "X4", "X5", "X6", "X7"}


def test_nondeterministic_function_fails_reproducibility() -> None:
    rng = random.Random(7)  # hidden state: each call returns a slightly different function
    g = lambda x: x - 0.5 + 1e-9 * rng.random()  # noqa: E731
    root = Root(0.5, "crossing", 0.495, 0.5, -0.005, 0.0, 0.0, 1, True, "below")
    x4 = status(fz.falsify_breakeven(g, root, 0.0, 1.0, grid_points=201, xtol=1e-12), "X4")
    assert x4.status == "failed" and x4.category == "not_reproduced" and x4.metrics["n_distinct_outcomes"] > 1
    counter = itertools.count()
    g2 = lambda x: x - 0.5 + 1e-9 * next(counter)  # noqa: E731
    assert status(fz.falsify_breakeven(g2, root, 0.0, 1.0, grid_points=201, xtol=1e-12), "X4").status == "failed"


# ---------------------------------------------------------------- independent roots


def test_independent_path_disagreement_or_absence_fails_the_candidate() -> None:
    g, root = e2_root()
    kw = dict(grid_points=N, xtol=XTOL)
    far = fz.falsify_breakeven(g, root, LO, HI, reference_roots=[1.2], **kw)  # type: ignore[arg-type]
    none = fz.falsify_breakeven(g, root, LO, HI, reference_roots=[], **kw)  # type: ignore[arg-type]
    assert status(far, "X6").status == "failed" and status(none, "X6").status == "failed"
    assert status(none, "X6").category == "not_independently_reproduced"


# ---------------------------------------------------------------- insufficient information is never a pass


def test_missing_inputs_give_skipped_or_inconclusive_never_passed() -> None:
    rep = run(lambda x: x - 0.5, 0.0, 1.0)
    assert status(rep, "X5").status == "skipped" and status(rep, "X6").status == "skipped"
    root = find_breakevens(lambda x: x - 0.5, 0.0, 1.0, 201, 1e-12).roots[0]

    def out_of_domain(_over):  # type: ignore[no-untyped-def]
        def g(_x: float) -> float:
            raise PhysicalConstraintError("invalid input")

        return g

    r1 = fz.falsify_breakeven(lambda x: x - 0.5, root, 0.0, 1.0, grid_points=201, xtol=1e-12, make_g=out_of_domain,
                              base={"p": 1.0}, search_var="x", perturb_vars=["p"])
    x5 = status(r1, "X5")
    assert x5.status == "inconclusive" and x5.metrics["n_invalid"] == 6

    r2 = fz.falsify_breakeven(lambda x: x - 0.5, root, 0.0, 1.0, grid_points=201, xtol=1e-12,
                              make_g=lambda o: (lambda x: x - 0.5), base={"p": 0.0}, search_var="x",
                              perturb_vars=["p"])
    assert status(r2, "X5").status == "inconclusive" and status(r2, "X5").metrics["skipped_zero_base"] == "p"

    unk = Root(0.5, "grid_zero", None, None, None, None, 0.0, 0, True, "unknown")
    r3 = fz.falsify_breakeven(lambda x: x - 0.5, unk, 0.0, 1.0, grid_points=201, xtol=1e-12,
                              make_g=lambda o: (lambda x: x - 0.5), base={"p": 1.0}, search_var="x",
                              perturb_vars=["p"])
    assert status(r3, "X5").status == "inconclusive"


def test_range_too_narrow_for_probes_is_inconclusive_not_passed() -> None:
    g = lambda x: x - 0.5  # noqa: E731
    root = Root(0.5, "crossing", 0.4, 0.6, -0.1, 0.1, 0.0, 1, True, "below")
    # xtol large enough that the smallest probe (10 * xtol = 0.6) can never fit in [0, 1] around 0.5
    rep = fz.falsify_breakeven(g, root, 0.0, 1.0, grid_points=11, xtol=0.06)
    assert status(rep, "X1").status in ("inconclusive", "passed")
    cfg = fz.FalsificationConfig(probe_fractions=(0.45,))
    rep2 = fz.falsify_breakeven(g, Root(0.5, "crossing", 0.4, 0.6, -0.1, 0.1, 0.0, 1, True, "below"), 0.0, 1.0,
                                grid_points=11, xtol=0.06, config=cfg)
    assert status(rep2, "X1").status == "inconclusive"


# ---------------------------------------------------------------- invalid configurations


def test_invalid_configurations_are_rejected() -> None:
    bad_cfgs = [
        dict(probe_fractions=()),
        dict(probe_fractions=(1e-2, 1e-4)),
        dict(probe_fractions=(0.6,)),
        dict(edge_margin=0.5),
        dict(edge_margin=-0.1),
        dict(repeats=1),
        dict(perturbation_levels=(0.0,)),
        dict(perturbation_levels=(1.0,)),
        dict(perturbation_levels=(0.1, 0.05)),
        dict(dense_factor=1),
        dict(agree_tol=0.0),
        dict(noise_rel=float("nan")),
        dict(max_continuity_ratio=1.0),
        dict(range_expansion=0.0),
    ]
    g = lambda x: x - 0.5  # noqa: E731
    root = Root(0.5, "crossing", 0.495, 0.5, -0.005, 0.0, 0.0, 1, True, "below")
    for kw in bad_cfgs:
        cfg = fz.FalsificationConfig(**kw)  # type: ignore[arg-type]
        assert raises_config(fz.falsify_breakeven, g, root, 0.0, 1.0, grid_points=201, xtol=1e-12, config=cfg), kw
        assert raises_config(fz.falsify_no_crossing, g, 0.0, 1.0, grid_points=201, xtol=1e-12, config=cfg), kw


def test_invalid_search_arguments_are_rejected() -> None:
    g = lambda x: x - 0.5  # noqa: E731
    root = Root(0.5, "crossing", 0.495, 0.5, -0.005, 0.0, 0.0, 1, True, "below")
    k = dict(grid_points=201, xtol=1e-12)
    assert raises_config(fz.falsify_breakeven, g, root, 1.0, 0.0, **k)
    assert raises_config(fz.falsify_breakeven, g, root, 0.0, 1.0, grid_points=2, xtol=1e-12)
    assert raises_config(fz.falsify_breakeven, g, root, 0.0, 1.0, grid_points=201, xtol=0.0)
    assert raises_config(fz.falsify_breakeven, g, root, 0.6, 1.0, **k)  # root outside the range
    assert raises_config(fz.falsify_breakeven, g, root, 0.0, 1.0, make_g=lambda o: g, **k)
    assert raises_config(fz.falsify_breakeven, g, root, 0.0, 1.0, make_g=lambda o: g, base={"p": 1.0},
                         search_var="x", perturb_vars=["q"], **k)
    assert raises_config(fz.falsify_no_crossing, g, 1.0, 1.0, **k)


# ---------------------------------------------------------------- gaps found by mutation testing


def test_jump_is_caught_by_the_continuity_ratio_even_if_the_root_is_mislabelled_as_a_crossing() -> None:
    g = lambda x: -1.0 if x < 0.5 else 0.5  # noqa: E731  # opposite signs, but |g(x*)| = 0.5 of the side values
    mislabelled = Root(0.5, "crossing", 0.495, 0.5, -1.0, 0.5, 0.0, 1, True, "below")
    x1 = status(fz.falsify_breakeven(g, mislabelled, 0.0, 1.0, grid_points=201, xtol=1e-12), "X1")
    assert x1.status == "failed" and math.isclose(x1.metrics["continuity_ratio"], 0.5)


def test_perturbation_that_reverses_the_direction_of_the_crossing_does_not_preserve_the_finding() -> None:
    def make(over):  # type: ignore[no-untyped-def]
        slope = over.get("p", 1.005) - 1.0  # sign of the slope flips when p drops below 1
        return lambda x: slope * (x - 0.5003)  # same location of the root, opposite beneficial side

    g = make({})
    root = find_breakevens(g, 0.0, 1.0, 201, 1e-12).roots[0]
    assert root.kind == "crossing" and root.beneficial_side == "below"
    rep = fz.falsify_breakeven(g, root, 0.0, 1.0, grid_points=201, xtol=1e-12, make_g=make,
                               base={"p": 1.005}, search_var="x", perturb_vars=["p"])
    x5 = status(rep, "X5")
    assert x5.status == "failed" and x5.metrics["preserved_fraction@0.01"] == 0.5
    assert x5.metrics["lost"] == "p-0.01,p-0.05,p-0.1"  # location unchanged; direction reversed => not preserved


def test_reported_root_that_independent_solvers_do_not_reproduce_fails_stability() -> None:
    g = lambda x: x - 0.5  # noqa: E731
    off = Root(0.5 + 1e-7, "crossing", 0.4, 0.6, -0.1, 0.1, 1e-7, 3, True, "below")  # 1e-7 x w away from truth
    x2 = status(fz.falsify_breakeven(g, off, 0.0, 1.0, grid_points=201, xtol=1e-12), "X2")
    assert x2.status == "failed" and "independent calculation paths disagree" in x2.summary
    assert x2.metrics["noise_shift_over_w"] < 1e-6  # only the path comparison can detect this
