import math

import pytest
from conftest import make_config, with_binding

from ecobackfire.contracts.experiment import Binding
from ecobackfire.core.engine import prepare_inputs
from ecobackfire.errors import (
    ConfigError,
    EvidenceNotAvailableError,
    MissingEvidenceError,
    PhysicalConstraintError,
    UnitError,
)
from ecobackfire.experiments.runner import run_experiment
from ecobackfire.models.registry import get_model
from ecobackfire.units import to_canonical


def test_unit_conversions():
    assert to_canonical(3.6, "MJ/yr", "kWh/yr") == pytest.approx(1.0)
    assert to_canonical(400.0, "gCO2e/kWh", "kgCO2e/kWh") == pytest.approx(0.4)
    assert to_canonical(1.2, "tCO2e", "kgCO2e") == pytest.approx(1200.0)
    assert to_canonical(144.0, "month", "yr") == pytest.approx(12.0)


@pytest.mark.parametrize(
    "value,unit,canon",
    [
        (1.0, "kg", "kWh/yr"),  # incompatible dimension
        (1.0, "kWh", "kgCO2e"),
        (1.0, "furlong_per_fortnight_squared_banana", "kWh/yr"),  # unknown unit
    ],
)
def test_incompatible_or_unknown_units_rejected(value, unit, canon):
    with pytest.raises(UnitError):
        to_canonical(value, unit, canon)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_values_rejected(bad):
    with pytest.raises(UnitError):
        to_canonical(bad, "kWh/yr", "kWh/yr")


def test_binding_with_incompatible_unit_never_reaches_model(e2_config):
    cfg = with_binding(e2_config, "E0", unit="kg")
    with pytest.raises(UnitError):
        run_experiment(cfg)


def test_missing_binding_is_an_error_not_zero(e2_config):
    bindings = {k: v.model_dump(mode="json") for k, v in e2_config.bindings.items() if k != "EM1"}
    cfg = make_config(e2_config, bindings=bindings)
    with pytest.raises(MissingEvidenceError, match="EM1"):
        run_experiment(cfg)


def test_explicit_missing_marker_is_an_error_not_zero(e2_config):
    cfg = with_binding(e2_config, "f", value=None, missing=True)
    with pytest.raises(MissingEvidenceError):
        run_experiment(cfg)


def test_binding_rejects_value_with_missing_flag():
    with pytest.raises(ValueError):
        Binding(value=1.0, missing=True, unit="yr", provenance_class="user_supplied")


def test_unknown_binding_id_rejected(e2_config):
    bindings = {k: v.model_dump(mode="json") for k, v in e2_config.bindings.items()}
    bindings["E_0"] = bindings["E0"]
    with pytest.raises(ConfigError, match="unknown inputs"):
        run_experiment(make_config(e2_config, bindings=bindings))


def test_sourced_claim_without_evidence_store_is_refused(e2_config):
    cfg = with_binding(e2_config, "f", provenance_class="sourced", evidence_record_id="rec-1", note=None)
    with pytest.raises(EvidenceNotAvailableError):
        run_experiment(cfg)


def test_sourced_requires_record_id():
    with pytest.raises(ValueError):
        Binding(value=1.0, unit="yr", provenance_class="sourced")


def test_illustrative_requires_marker():
    with pytest.raises(ValueError):
        Binding(value=1.0, unit="yr", provenance_class="illustrative", note="just a guess")


@pytest.mark.parametrize(
    "vid,bad", [("eta", 1.0), ("eta", -0.1), ("phi", 0.0), ("epsilon", -1.0), ("L", 0.0), ("E0", 0.0), ("f", -0.4)]
)
def test_physical_bounds_rejected(e2_config, vid, bad):
    cfg = with_binding(e2_config, vid, value=bad)
    with pytest.raises(PhysicalConstraintError):
        run_experiment(cfg)


def test_applicability_warning_not_silent(e2_config):
    cfg = with_binding(e2_config, "L", value=100.0)  # outside model-declared validity range
    res = run_experiment(cfg)
    assert any("applicability" in w and "L=" in w for w in res.warnings)


def test_exact_model_ref_required(e2_config):
    with pytest.raises(ValueError):
        make_config(e2_config, model_ref="efficiency_rebound@latest")
    with pytest.raises(ConfigError):
        get_model("efficiency_rebound@9.9.9")


def test_prepare_inputs_returns_canonical_units(e2_config):
    model = get_model(e2_config.model_ref)
    p = prepare_inputs(model.spec, e2_config.bindings, model.required_inputs({}))
    assert p.values["E0"] == pytest.approx(3000.0)
