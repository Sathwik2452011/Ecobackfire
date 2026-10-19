"""Break-even detection against functions with KNOWN solutions, no-crossing, boundary and invalid cases."""

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ecobackfire.errors import ConfigError, NumericalError
from ecobackfire.experiments.breakeven import find_breakevens


def test_linear_known_root():
    sc = find_breakevens(lambda x: x - 2.0, 0.0, 5.0, n=52)  # 2.0 is not a grid point
    assert sc.classification == "crossing"
    assert len(sc.roots) == 1
    r = sc.roots[0]
    assert r.value == pytest.approx(2.0, abs=1e-11)
    assert r.beneficial_side == "below" and r.g_low < 0 < r.g_high and r.converged


def test_decreasing_function_flips_beneficial_side():
    sc = find_breakevens(lambda x: 3.0 - x, 0.0, 5.0, n=51)
    assert sc.roots[0].value == pytest.approx(3.0, abs=1e-11)
    assert sc.roots[0].beneficial_side == "above"


def test_two_roots_found_and_ordered():
    sc = find_breakevens(lambda x: (x - 1.0) * (x - 3.0), 0.0, 4.0, n=81)
    assert [round(r.value, 9) for r in sc.roots] == [1.0, 3.0]
    assert [r.beneficial_side for r in sc.roots] == ["above", "below"]


def test_root_exactly_on_a_grid_point():
    sc = find_breakevens(lambda x: x - 2.0, 0.0, 4.0, n=5)  # grid 0,1,2,3,4
    assert [r.kind for r in sc.roots] == ["grid_zero"]
    assert sc.roots[0].value == 2.0 and sc.roots[0].beneficial_side == "below"


def test_root_at_lower_endpoint():
    sc = find_breakevens(lambda x: x, 0.0, 1.0, n=11)
    assert sc.roots[0].value == 0.0 and sc.roots[0].kind == "grid_zero"
    # g increases through 0, so the g<0 ('beneficial') side is below the root, outside the range
    assert sc.roots[0].beneficial_side == "below"


def test_root_at_upper_endpoint():
    sc = find_breakevens(lambda x: x - 1.0, 0.0, 1.0, n=11)
    assert sc.roots[0].value == 1.0 and sc.roots[0].beneficial_side == "below"


def test_no_crossing_never_beneficial():
    sc = find_breakevens(lambda x: x * x + 1.0, -2.0, 2.0)
    assert sc.classification == "no_crossing_never_beneficial" and sc.roots == []
    assert "NOT beneficial" in sc.note


def test_no_crossing_always_beneficial():
    sc = find_breakevens(lambda x: -1.0 - x * x, -2.0, 2.0)
    assert sc.classification == "no_crossing_always_beneficial" and sc.roots == []


def test_discontinuity_is_flagged_not_reported_as_a_clean_root():
    sc = find_breakevens(lambda x: -1.0 if x < 1.5 else 1.0, 0.0, 3.0, n=31)
    assert len(sc.roots) == 1
    assert sc.roots[0].kind == "discontinuity_suspected"
    assert sc.warnings


def test_documented_limitation_even_multiplicity_root_is_missed():
    # (x-2)^2 touches zero at 2 but never changes sign; with 2 off-grid the method cannot see it.
    sc = find_breakevens(lambda x: (x - 2.0) ** 2, 0.0, 4.0, n=6)  # grid step 0.8; 2.0 not on grid
    assert sc.roots == []
    assert "narrower than the grid" in sc.note  # the limitation is stated in the output itself


def test_narrow_dip_between_grid_points_is_missed_but_dense_grid_finds_it():
    g = lambda x: -1.0 if 1.1 <= x <= 1.12 else 1.0  # noqa: E731
    coarse = find_breakevens(g, 0.0, 3.0, n=7)
    fine = find_breakevens(g, 0.0, 3.0, n=3001)
    assert coarse.roots == [] and len(fine.roots) == 2


@pytest.mark.parametrize(
    "lo,hi,n,xtol",
    [
        (1.0, 1.0, 11, 1e-12),
        (2.0, 1.0, 11, 1e-12),
        (0.0, 1.0, 2, 1e-12),
        (0.0, 1.0, 11, 0.0),
        (0.0, 1.0, 11, -1e-9),
        (math.nan, 1.0, 11, 1e-12),
        (0.0, math.inf, 11, 1e-12),
    ],
)
def test_invalid_search_inputs_rejected(lo, hi, n, xtol):
    with pytest.raises(ConfigError):
        find_breakevens(lambda x: x, lo, hi, n, xtol)


def test_non_finite_function_values_refused():
    with pytest.raises(NumericalError):
        find_breakevens(lambda x: math.nan if x > 0.5 else -1.0, 0.0, 1.0)
    with pytest.raises(NumericalError):
        find_breakevens(lambda x: math.inf if x > 0.5 else -1.0, 0.0, 1.0)


@settings(max_examples=200, deadline=None)
@given(
    root=st.floats(min_value=0.05, max_value=9.95), slope=st.floats(min_value=0.1, max_value=50.0), negate=st.booleans()
)
def test_property_linear_roots_are_recovered(root, slope, negate):
    sign = -1.0 if negate else 1.0
    sc = find_breakevens(lambda x: sign * slope * (x - root), 0.0, 10.0, n=101)
    assert len(sc.roots) == 1
    assert sc.roots[0].value == pytest.approx(root, abs=1e-9)
    assert sc.roots[0].beneficial_side == ("above" if negate else "below")
