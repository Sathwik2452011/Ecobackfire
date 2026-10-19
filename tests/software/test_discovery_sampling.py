"""Seeded QMC sampling + rank-correlation screening. Pure numerics; no pytest import.

Oracle: the closed-form break-even elasticity eps*(eta, phi, r) from the independent reference.
"""

import math

import numpy as np

from ecobackfire.discovery import sampling as sp
from ecobackfire.errors import ConfigError, NumericalError
from ecobackfire.verification.reference import rebound_analytical as ana

E2 = dict(E0=3000.0, eta=0.30, phi=0.80, epsilon=0.5, f=0.40, EM0=1000.0, EM1=1720.0, L=12.0, s_max=1.5)
NAMES = ["E0", "eta", "phi", "f", "EM0", "EM1", "L"]
LOWS = [2000.0, 0.2, 0.5, 0.1, 500.0, 1000.0, 8.0]
HIGHS = [4500.0, 0.4, 1.0, 0.8, 1500.0, 2500.0, 16.0]


def eps_star_of(p: np.ndarray) -> float | None:
    i = {**E2, **dict(zip(NAMES, map(float, p), strict=True))}
    return ana.eps_star(i["eta"], i["phi"], ana.embodied_burden_r(i, "B1"))


def _raises(exc, fn, *a) -> bool:  # type: ignore[no-untyped-def]
    try:
        fn(*a)
    except exc:
        return True
    return False


def test_sobol_is_reproducible_and_seed_dependent() -> None:
    a, b, c = sp.sobol_unit(3, 6, 7), sp.sobol_unit(3, 6, 7), sp.sobol_unit(3, 6, 8)
    assert a.shape == (64, 3) and np.array_equal(a, b) and not np.array_equal(a, c)
    assert a.min() >= 0.0 and a.max() < 1.0


def test_sobol_is_well_spread() -> None:
    u = sp.sobol_unit(2, 8, 0)
    assert np.all(np.abs(u.mean(axis=0) - 0.5) < 0.02)  # 256 low-discrepancy points: means very near 0.5


def test_scale_points_stays_in_range() -> None:
    pts = sp.scale_points(sp.sobol_unit(7, 6, 1), LOWS, HIGHS)
    assert np.all(pts >= np.array(LOWS)) and np.all(pts <= np.array(HIGHS))


def test_invalid_sampling_inputs() -> None:
    assert _raises(ConfigError, sp.sobol_unit, 0, 4, 1)
    assert _raises(ConfigError, sp.sobol_unit, 2, 0, 1)
    assert _raises(ConfigError, sp.sobol_unit, 2, 21, 1)
    assert _raises(ConfigError, sp.sobol_unit, 2, 4, -1)
    assert _raises(ConfigError, sp.sobol_unit, 2, 4, True)
    u = sp.sobol_unit(2, 3, 0)
    assert _raises(ConfigError, sp.scale_points, u, [0.0, 1.0], [1.0, 1.0])  # low == high
    assert _raises(ConfigError, sp.scale_points, u, [0.0], [1.0])  # dimension mismatch
    assert _raises(ConfigError, sp.scale_points, u, [0.0, float("nan")], [1.0, 2.0])


def test_rank_correlation_exact_cases() -> None:
    pts = sp.scale_points(sp.sobol_unit(2, 7, 3), [0.0, 0.0], [1.0, 1.0])
    rho = sp.rank_correlations(pts, [3.0 * x + 1.0 for x in pts[:, 0]])
    assert math.isclose(rho[0], 1.0, abs_tol=1e-12) and abs(rho[1]) < 0.3
    rho = sp.rank_correlations(pts, [-x**3 for x in pts[:, 0]])  # monotone decreasing => rho = -1
    assert math.isclose(rho[0], -1.0, abs_tol=1e-12)


def test_rank_correlation_undefined_cases() -> None:
    pts = sp.scale_points(sp.sobol_unit(2, 4, 3), [0.0, 0.0], [1.0, 1.0])
    assert sp.rank_correlations(pts, [1.0] * 16) == [None, None]  # constant target
    assert sp.rank_correlations(pts, [None] * 16) == [None, None]  # nothing defined
    assert sp.rank_correlations(pts, [None] * 14 + [1.0, 2.0]) == [None, None]  # < 3 defined
    const_col = np.column_stack([np.ones(16), pts[:, 0]])
    rho = sp.rank_correlations(const_col, [float(i) for i in range(16)])
    assert rho[0] is None and rho[1] is not None


def test_failed_and_undefined_samples_are_counted_not_hidden() -> None:
    def f(p: np.ndarray) -> float | None:
        if p[0] < 0.25:
            raise NumericalError("boom")
        return None if p[0] > 0.9 else float(p[0])

    res = sp.sample_function(f, [0.0], [1.0], 6, 5)
    assert res.n_samples == 64 and res.n_failed > 0 and res.n_defined > 0
    n_undefined_ok = sum(v is None and not x for v, x in zip(res.values, res.failed, strict=True))
    assert res.n_failed + res.n_defined + n_undefined_ok == 64
    assert all(v is None for v, x in zip(res.values, res.failed, strict=True) if x)


def test_closed_form_sensitivity_signs_hold_for_every_seed() -> None:
    # Strictly monotone effects, valid whatever the sign of EM1-EM0:
    #   phi up -> denominator up -> eps* down;  EM1 up -> r up -> eps* down;  EM0 up -> r down -> eps* up.
    # f, L and E0 act through 1/(L f E0) and flip direction with the sign of EM1-EM0, so are NOT asserted.
    for seed in (0, 1, 2, 7, 42, 12345):
        res = sp.sample_function(eps_star_of, LOWS, HIGHS, 8, seed)
        rho = dict(zip(NAMES, sp.rank_correlations(res.points, res.values), strict=True))
        assert rho["phi"] < -0.6 and rho["EM1"] < -0.2 and rho["EM0"] > 0.1
        assert max(rho, key=lambda k: abs(rho[k])) == "phi"


def test_sensitivity_pipeline_is_repeatable_and_seed_sensitive() -> None:
    a = sp.sample_function(eps_star_of, LOWS, HIGHS, 7, 12345)
    b = sp.sample_function(eps_star_of, LOWS, HIGHS, 7, 12345)
    c = sp.sample_function(eps_star_of, LOWS, HIGHS, 7, 54321)
    assert a.values == b.values and np.array_equal(a.points, b.points)
    assert a.values != c.values
    assert sp.rank_correlations(a.points, a.values) == sp.rank_correlations(b.points, b.values)


def test_some_inputs_have_no_breakeven_at_all() -> None:
    # r > eta (embodied burden exceeds the efficiency gain) => no eps >= 0 makes the intervention beneficial.
    res = sp.sample_function(eps_star_of, LOWS, HIGHS, 8, 12345)
    assert 0 < res.n_samples - res.n_defined < res.n_samples * 0.1
