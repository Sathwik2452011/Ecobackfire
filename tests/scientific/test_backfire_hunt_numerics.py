"""Automated backfire hunt: numerical core on SYNTHETIC mathematical fixtures (no environmental data, no model).

g < 0 is beneficial, g > 0 adverse (the project's decision-function convention).
"""

from __future__ import annotations

import math

import pytest

from ecobackfire.discovery.backfire_search import HuntConfig, ParamTask, run_hunt

pytestmark = pytest.mark.scientific


def task(g, lo=0.0, hi=10.0, n=101, var="x", xtol=1e-12):  # type: ignore[no-untyped-def]
    return ParamTask(var, g, lo, hi, n, xtol, 1.0)


def hunt(g, budget=100000, **kw):  # type: ignore[no-untyped-def]
    return run_hunt([task(g, **kw)], HuntConfig(max_evaluations=budget))


def roots(out):  # type: ignore[no-untyped-def]
    return [h.root.value for h in out.params[0].roots]


def test_sign_change_benefit_to_backfire_found() -> None:
    out = hunt(lambda x: x - 3.7)  # beneficial below 3.7, adverse above
    assert out.status == "complete"
    assert roots(out) == [pytest.approx(3.7, abs=1e-9)]
    regimes = [iv.regime for iv in out.params[0].intervals]
    assert regimes == ["beneficial", "adverse"]


def test_no_crossing_remains_beneficial_and_never_beneficial() -> None:
    assert hunt(lambda x: -1.0 - x).params[0].behaviour == "remains_beneficial"
    assert hunt(lambda x: 1.0 + x).params[0].behaviour == "never_beneficial_in_range"


def test_multiple_separated_reversal_regions_non_monotone() -> None:
    # adverse on (2,3) and (6,7): two separated reversal regions, three beneficial intervals
    def g(x: float) -> float:
        return -math.cos(2 * math.pi * (x - 2.5) / 4.0) * 0 + (0.25 - min(abs(x - 2.5), abs(x - 6.5)))

    out = hunt(g)
    r = roots(out)
    assert len(r) == 4
    assert r == [pytest.approx(v, abs=1e-6) for v in (2.25, 2.75, 6.25, 6.75)]
    adverse = [iv for iv in out.params[0].intervals if iv.regime == "adverse"]
    assert len(adverse) == 2


def test_narrow_sub_grid_reversal_region_is_found() -> None:
    # grid spacing 0.1; adverse region (5.02, 5.06) is narrower than a cell and lies between grid points
    def g(x: float) -> float:
        return -1.0 + 2.0 * math.exp(-(((x - 5.04) / 0.01) ** 2)) * 1.0

    out = hunt(g)
    r = roots(out)
    assert len(r) == 2 and r[0] < 5.04 < r[1]
    assert all(h.origin == "local_refinement" for h in out.params[0].roots)
    assert any(iv.regime == "adverse" and (iv.high - iv.low) < 0.05 * 10.0 for iv in out.params[0].intervals)


def test_tangent_touch_is_break_even_without_reversal() -> None:
    out = hunt(lambda x: -((x - 5.0) ** 2))  # touches 0 at 5.0, never positive
    p = out.params[0]
    assert p.behaviour in ("touches_break_even", "remains_beneficial")
    assert not [h for h in p.roots]  # never reported as a reversal


def test_budget_exhausted_returns_valid_partial_result() -> None:
    out = run_hunt([task(lambda x: x - 3.0), task(lambda x: x - 4.0, var="y")], HuntConfig(max_evaluations=150))
    assert out.status == "budget_exhausted"
    assert out.evaluations <= 150  # the cap is exact
    assert out.params[0].status == "searched" and roots(out) == [pytest.approx(3.0)]
    assert out.params[1].status == "budget_exhausted"  # recorded, not silently dropped


def test_budget_never_exceeded_even_mid_search() -> None:
    for cap in (1, 50, 101, 120, 300, 1000):
        out = hunt(lambda x: x - 3.0, budget=cap)
        assert out.evaluations <= cap


def test_deterministic_repeat() -> None:
    def g(x: float) -> float:
        return math.sin(3 * x) * 0.5 + 0.1 * x - 0.3

    a, b = hunt(g), hunt(g)
    assert roots(a) == roots(b) and a.evaluations == b.evaluations
    assert [(i.low, i.high, i.regime) for i in a.params[0].intervals] == [
        (i.low, i.high, i.regime) for i in b.params[0].intervals
    ]


def test_evaluation_counts_reported() -> None:
    out = hunt(lambda x: x - 3.0, n=101)
    assert out.evaluations >= 101 and out.params[0].evaluations == out.evaluations
