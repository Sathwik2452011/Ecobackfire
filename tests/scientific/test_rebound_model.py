"""Analytical and invariant tests of efficiency_rebound@1.0.0 (model VERIFICATION, not validation)."""

import math

import pytest
from conftest import physical_inputs
from hypothesis import given, settings
from hypothesis import strategies as st

from ecobackfire.core.trace import Trace
from ecobackfire.errors import ConfigError, MissingEvidenceError, NumericalError, PhysicalConstraintError
from ecobackfire.models.registry import get_model

M = get_model("efficiency_rebound@1.0.0")
CE = {"boundary_variant": "B1", "demand_form": "constant_elasticity"}


def ev(opts=None, **kw):
    return M.evaluate(physical_inputs(**kw), opts or CE)


def test_hand_calculated_no_rebound():
    o = ev(eta=0.25, epsilon=0.0)
    assert o["E1"] == pytest.approx(750.0) and o["dC"] == pytest.approx(-125.0)


def test_full_rebound_when_phi_one_eps_one():
    o = ev(eta=0.3, phi=1.0, epsilon=1.0)
    assert o["E1"] == pytest.approx(1000.0, rel=1e-12) and o["dC"] == pytest.approx(0.0, abs=1e-9)
    assert o["rebound_R"] == pytest.approx(1.0)


def test_eta_zero_changes_only_embodied_term():
    o = ev(eta=0.0, epsilon=0.7, phi=0.5, EM0=300.0, EM1=800.0)
    assert o["E1"] == 1000.0 and o["dC"] == pytest.approx(50.0)


def test_boundary_variants_change_the_answer_visibly():
    kw = dict(eta=0.0, EM0=300.0, EM1=800.0)
    b1 = ev({"boundary_variant": "B1", "demand_form": "constant_elasticity"}, **kw)["dC"]
    b2 = ev({"boundary_variant": "B2", "demand_form": "constant_elasticity"}, **kw)["dC"]
    b3 = ev({"boundary_variant": "B3", "demand_form": "constant_elasticity"}, **kw)["dC"]
    assert (b1, b2, b3) == (pytest.approx(50.0), pytest.approx(80.0), 0.0)
    assert M.system_boundary({"boundary_variant": "B2"}).id == "B2"
    assert "embodied emissions" in " ".join(M.system_boundary({"boundary_variant": "B3"}).excluded_stages)


def test_functional_unit_divergence_exists():
    o = ev(eta=0.3, phi=0.8, epsilon=2.0, f=0.4, E0=3000.0)
    assert o["dC"] > 0 and o["intensity_ratio"] < 1  # absolute emissions rise, per-service falls


def test_saturation_caps_service_response():
    o = ev({"boundary_variant": "B1", "demand_form": "saturating"}, eta=0.3, phi=1.0, epsilon=5.0, s_max=1.2)
    assert o["service_ratio"] == pytest.approx(1.2)
    assert o["E1"] == pytest.approx(0.7 * 1.2 * 1000.0)


def test_trace_cites_equation_ids():
    tr = Trace()
    M.evaluate(physical_inputs(eta=0.3, epsilon=0.5), CE, trace=tr)
    assert {s["equation"] for s in tr.steps} >= {"EQ1", "EQ2", "EQ3", "EQ4", "EQ5", "EQ6", "EQ7", "EQ8"}
    declared = {e.id for e in M.spec.equations}
    assert {s["equation"] for s in tr.steps} <= declared


@pytest.mark.parametrize(
    "mod",
    [
        {"eta": 1.0},
        {"eta": -1e-9},
        {"phi": 0.0},
        {"phi": 1.0001},
        {"epsilon": -0.1},
        {"L": 0.0},
        {"E0": 0.0},
        {"f": -1.0},
        {"EM1": -1.0},
        {"E0": math.nan},
        {"f": math.inf},
    ],
)
def test_invalid_states_rejected(mod):
    with pytest.raises(PhysicalConstraintError):
        ev(**mod)


def test_overflow_is_a_typed_error_not_inf():
    with pytest.raises(NumericalError):
        ev(eta=0.3, phi=1.0, epsilon=1e6)


def test_missing_required_input_is_an_error():
    inp = physical_inputs()
    del inp["L"]
    with pytest.raises(MissingEvidenceError):
        M.evaluate(inp, CE)


def test_saturating_requires_s_max():
    inp = physical_inputs()
    del inp["s_max"]
    with pytest.raises(MissingEvidenceError):
        M.evaluate(inp, {"boundary_variant": "B1", "demand_form": "saturating"})


def test_unknown_option_values_rejected():
    with pytest.raises(ConfigError):
        M.resolve_options({"boundary_variant": "B9"})
    with pytest.raises(ConfigError):
        M.resolve_options({"nonsense": "x"})


def test_determinism_bitwise_same_inputs():
    a = ev(eta=0.3, phi=0.8, epsilon=1.3, EM1=250.0)
    b = ev(eta=0.3, phi=0.8, epsilon=1.3, EM1=250.0)
    assert a == b


@settings(max_examples=150, deadline=None)
@given(
    eta=st.floats(0.0, 0.95),
    phi=st.floats(0.01, 1.0),
    e1=st.floats(0.0, 5.0),
    e2=st.floats(0.0, 5.0),
    em1=st.floats(0.0, 5000.0),
)
def test_property_dC_nondecreasing_in_epsilon(eta, phi, e1, e2, em1):
    lo, hi = sorted((e1, e2))
    d_lo = ev(eta=eta, phi=phi, epsilon=lo, EM1=em1)["dC"]
    d_hi = ev(eta=eta, phi=phi, epsilon=hi, EM1=em1)["dC"]
    assert d_hi >= d_lo - 1e-9 * max(1.0, abs(d_lo))


@settings(max_examples=100, deadline=None)
@given(k=st.floats(0.1, 50.0), eta=st.floats(0.0, 0.9), eps=st.floats(0.0, 3.0), em1=st.floats(0.0, 3000.0))
def test_property_dC_scales_linearly_with_size(k, eta, eps, em1):
    base = ev(eta=eta, epsilon=eps, EM1=em1)["dC"]
    scaled = M.evaluate(physical_inputs(eta=eta, epsilon=eps, EM1=k * em1, E0=k * 1000.0), CE)["dC"]
    assert scaled == pytest.approx(k * base, rel=1e-9, abs=1e-9)
