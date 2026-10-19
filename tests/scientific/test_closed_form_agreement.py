"""Three-way agreement on break-evens: production numeric root vs closed form vs 60-digit Decimal bisection."""

import pytest
from conftest import physical_inputs
from hypothesis import given, settings
from hypothesis import strategies as st

from ecobackfire.experiments.breakeven import find_breakevens
from ecobackfire.experiments.runner import make_g
from ecobackfire.models.registry import get_model
from ecobackfire.verification.reference import rebound_analytical as ana
from ecobackfire.verification.reference import rebound_reference as ref

M = get_model("efficiency_rebound@1.0.0")
METRIC = M.spec.decision_metrics[0]
CE = {"boundary_variant": "B1", "demand_form": "constant_elasticity"}
LO, HI, N = 0.0, 20.0, 801


def numeric_roots(inputs, opts=CE, var="epsilon", lo=LO, hi=HI, n=N):
    return find_breakevens(make_g(M, opts, METRIC, inputs, var), lo, hi, n, 1e-12).roots


@pytest.mark.parametrize(
    "eta,phi,r,expected",
    [
        (0.2, 1.0, 0.0, 1.0),
        (0.5, 1.0, 0.0, 1.0),
        (0.9, 1.0, 0.0, 1.0),  # classical backfire threshold
        (0.2, 0.5, 0.0, 2.117904889901081),
        (0.3, 0.8, 0.05, 1.1127574680079595),
        (0.5, 1.0, 0.1, 0.8479969065549501),
    ],
)
def test_known_values(eta, phi, r, expected):
    inp = physical_inputs(eta=eta, phi=phi, EM1=r * 5000.0)
    roots = numeric_roots(inp)
    assert len(roots) == 1 and roots[0].value == pytest.approx(expected, rel=1e-9)
    assert ana.eps_star(eta, phi, r) == pytest.approx(expected, rel=1e-12)


@settings(max_examples=120, deadline=None)
@given(eta=st.floats(0.02, 0.9), phi=st.floats(0.05, 1.0), frac=st.floats(0.0, 0.99))
def test_property_three_way_agreement_when_a_root_exists(eta, phi, frac):
    r = frac * eta  # r <= eta  => break-even exists for eps >= 0
    inp = physical_inputs(eta=eta, phi=phi, EM1=r * 5000.0)
    cf = ana.eps_star(eta, phi, r)
    assert cf is not None
    num = numeric_roots(inp)
    if cf > HI - 1e-6:
        assert num == []  # root beyond the search range
        return
    dec = ref.breakevens(inp, CE, "epsilon", LO, HI, N)
    assert len(num) == len(dec) == 1
    assert num[0].value == pytest.approx(cf, rel=1e-8, abs=1e-9)
    assert float(dec[0]) == pytest.approx(cf, rel=1e-9, abs=1e-9)


@settings(max_examples=60, deadline=None)
@given(eta=st.floats(0.02, 0.9), phi=st.floats(0.05, 1.0), extra=st.floats(1.02, 5.0))
def test_property_no_break_even_when_embodied_burden_exceeds_eta(eta, phi, extra):
    r = min(extra * eta, 0.98)
    if r <= eta:
        return
    inp = physical_inputs(eta=eta, phi=phi, EM1=r * 5000.0)
    assert ana.eps_star(eta, phi, r) is None
    sc = find_breakevens(make_g(M, CE, METRIC, inp, "epsilon"), LO, HI, N, 1e-12)
    assert sc.roots == [] and sc.classification == "no_crossing_never_beneficial"


@settings(max_examples=60, deadline=None)
@given(eta=st.floats(0.05, 0.8), phi=st.floats(0.1, 1.0), eps=st.floats(0.0, 2.0), em1=st.floats(10.0, 2000.0))
def test_property_break_even_emissions_factor(eta, phi, eps, em1):
    inp = physical_inputs(eta=eta, phi=phi, epsilon=eps, EM1=em1)
    pred = ana.predicted_f_roots(inp, CE)
    roots = numeric_roots(inp, var="f", lo=0.0, hi=20.0)
    pred = [p for p in pred if 1e-9 < p < 20.0 - 1e-9]
    assert len(roots) == len(pred)
    for a, b in zip(roots, pred, strict=True):
        assert a.value == pytest.approx(b, rel=1e-8, abs=1e-10)


def test_decimal_reference_matches_production_pointwise():
    inp = physical_inputs(eta=0.3, phi=0.8, epsilon=1.1, EM0=200.0, EM1=900.0)
    for b in ("B1", "B2", "B3"):
        opts = {"boundary_variant": b, "demand_form": "constant_elasticity"}
        prod = M.evaluate(inp, opts)
        r = ref.evaluate(inp, opts)
        for k in ("E1", "C0", "C1", "dC", "service_ratio"):
            assert prod[k] == pytest.approx(float(r[k]), rel=1e-12, abs=1e-9)


def test_saturation_existence_condition_matches_numeric():
    # reversal exists iff (1-eta)*s_max > 1 - r
    for s_max, exists in [(1.2, False), (1.35, False), (1.36, True), (2.0, True)]:
        inp = physical_inputs(eta=0.3, phi=1.0, EM1=0.05 * 5000.0, s_max=s_max)
        opts = {"boundary_variant": "B1", "demand_form": "saturating"}
        roots = numeric_roots(inp, opts=opts, hi=10.0, n=1001)
        assert bool(roots) is exists, (s_max, [r.value for r in roots])
        assert bool(ana.predicted_epsilon_roots(inp, opts)) is exists
