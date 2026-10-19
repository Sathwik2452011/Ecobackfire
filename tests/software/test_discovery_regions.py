"""2-D region scanner: pure numerics, no pydantic and no pytest import (runnable anywhere numpy/scipy exist).

Oracles are the independent closed forms in verification.reference and hand-checkable linear cases.
"""

import math

from ecobackfire.discovery.regions import min_normalized_distance, scan_region
from ecobackfire.errors import ConfigError
from ecobackfire.verification.reference import rebound_analytical as ana
from ecobackfire.verification.reference import rebound_reference as ref

E2 = dict(E0=3000.0, eta=0.30, phi=0.80, epsilon=0.5, f=0.40, EM0=1000.0, EM1=1720.0, L=12.0, s_max=1.5)
OPTS = dict(boundary_variant="B1", demand_form="constant_elasticity")
R = ana.embodied_burden_r(E2, "B1")


def g_phi_eps(phi: float, eps: float) -> float:
    """dC / (f*E0) for the rebound model: closed form, independent of production code."""
    return (1 - E2["eta"]) * (1 - phi * E2["eta"]) ** (-eps) - 1 + R


def _raises_config(fn, *a) -> bool:  # type: ignore[no-untyped-def]
    try:
        fn(*a)
    except ConfigError:
        return True
    return False


def test_linear_region_counts_and_boundary_are_exact() -> None:
    # 9 points per axis => step 0.125 is exactly representable, so x + y - 1 is exactly 0 on the anti-diagonal
    sc = scan_region(lambda x, y: x + y - 1.0, 0.0, 1.0, 9, 0.0, 1.0, 9)
    assert (sc.n_beneficial, sc.n_not_beneficial, sc.n_zero) == (36, 36, 9)
    assert sc.n_cells == 81 and sc.classification == "reversal_region"
    assert sc.boundary and all(abs(x + y - 1.0) < 1e-12 for x, y in sc.boundary)
    assert not sc.suspected_discontinuities


def test_boundary_matches_rebound_closed_form() -> None:
    sc = scan_region(g_phi_eps, 0.5, 1.0, 21, 0.0, 3.0, 41)
    assert sc.classification == "reversal_region"
    assert len(sc.boundary) == 35  # 21 column-scan roots + 14 row-scan roots (rows with eps in the boundary span)
    for phi, eps in sc.boundary:
        assert math.isclose(ana.eps_star(E2["eta"], phi, R), eps, rel_tol=1e-9)


def test_boundary_points_are_zeros_of_the_decimal_reference_model() -> None:
    sc = scan_region(g_phi_eps, 0.5, 1.0, 21, 0.0, 3.0, 41)
    for phi, eps in sc.boundary[::5]:
        out = ref.evaluate({**E2, "phi": phi, "epsilon": eps}, OPTS)  # independent Decimal arithmetic
        assert abs(float(out["dC"]) / float(out["C0"])) < 1e-9


def test_always_and_never_beneficial_regions() -> None:
    # eps*(phi) spans about 0.856 (phi=1) to 1.88 (phi=0.5) for these inputs
    assert scan_region(g_phi_eps, 0.5, 1.0, 11, 0.0, 0.8, 11).classification == "always_beneficial"
    assert scan_region(g_phi_eps, 0.5, 1.0, 11, 2.0, 3.0, 11).classification == "never_beneficial"
    assert scan_region(g_phi_eps, 0.5, 1.0, 11, 0.0, 0.8, 11).boundary == []


def test_all_zero_function_is_indeterminate() -> None:
    sc = scan_region(lambda x, y: 0.0, 0.0, 1.0, 5, 0.0, 1.0, 5)
    assert sc.classification == "indeterminate" and sc.n_zero == 25


def test_jump_discontinuity_is_not_reported_as_boundary() -> None:
    sc = scan_region(lambda x, y: -1.0 if x < 0.5 else 1.0, 0.0, 1.0, 10, 0.0, 1.0, 10)
    assert sc.classification == "reversal_region"
    assert sc.boundary == [] and sc.suspected_discontinuities  # |g| stays 1 at the "root"


def test_vertical_boundary_is_found_by_the_row_scan() -> None:
    # g depends on x only: columns see no sign change, rows do. x* = 0.3 (not on the grid)
    sc = scan_region(lambda x, y: x - 0.3, 0.0, 1.0, 10, 0.0, 1.0, 10)
    assert sc.boundary and all(abs(x - 0.3) < 1e-9 for x, _ in sc.boundary)


def test_scan_is_deterministic() -> None:
    a = scan_region(g_phi_eps, 0.5, 1.0, 11, 0.0, 3.0, 21)
    b = scan_region(g_phi_eps, 0.5, 1.0, 11, 0.0, 3.0, 21)
    assert a == b


def test_distance_to_boundary() -> None:
    sc = scan_region(lambda x, y: x + y - 1.0, 0.0, 1.0, 9, 0.0, 1.0, 9)
    # base (0.25, 0.25): distance to the line x + y = 1 is 0.5*sqrt(2)/... ; grid points give an upper bound
    d = min_normalized_distance(sc.boundary, 0.25, 0.25, 0.0, 1.0, 0.0, 1.0)
    true = abs(0.25 + 0.25 - 1.0) / math.sqrt(2)
    assert d is not None and true - 1e-12 <= d <= true + 0.125  # upper bound, tight to the grid step
    assert min_normalized_distance([], 0.0, 0.0, 0.0, 1.0, 0.0, 1.0) is None
    # normalisation: same geometry in a 10x wider x-range must give the same distance
    sc2 = scan_region(lambda x, y: x / 10 + y - 1.0, 0.0, 10.0, 9, 0.0, 1.0, 9)
    d2 = min_normalized_distance(sc2.boundary, 2.5, 0.25, 0.0, 10.0, 0.0, 1.0)
    assert d2 is not None and math.isclose(d2, d, rel_tol=1e-9)


def test_invalid_axes_are_config_errors() -> None:
    g = lambda x, y: x - y  # noqa: E731
    assert _raises_config(scan_region, g, 1.0, 0.0, 5, 0.0, 1.0, 5)
    assert _raises_config(scan_region, g, 0.0, 1.0, 2, 0.0, 1.0, 5)
    assert _raises_config(scan_region, g, 0.0, 1.0, 5, float("nan"), 1.0, 5)
    assert _raises_config(scan_region, g, 0.0, math.inf, 5, 0.0, 1.0, 5)
