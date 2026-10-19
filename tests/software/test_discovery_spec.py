"""DiscoverySpec contract and TOML loader."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ecobackfire.contracts.discovery import DiscoverySpec, ParameterRange, ScoringWeights
from ecobackfire.discovery.loader import load_discovery_spec
from ecobackfire.errors import ConfigError

ROOT = Path(__file__).resolve().parents[2]
D1 = ROOT / "examples" / "discovery" / "d1_rebound_breakeven_scan.toml"
MARK = "ILLUSTRATIVE — not evidence"

pytestmark = pytest.mark.software


def test_example_spec_loads_and_is_all_illustrative() -> None:
    spec = load_discovery_spec(D1)
    assert spec.model_ref == "efficiency_rebound@1.0.0"
    assert {p.variable_id for p in spec.parameters} == {"epsilon", "f", "EM1", "L", "eta", "s_max"}
    assert all(b.provenance_class.value == "illustrative" for b in spec.bindings.values())


def test_loader_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_discovery_spec(tmp_path / "nope.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("not = [valid")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_discovery_spec(bad)
    incomplete = tmp_path / "inc.toml"
    incomplete.write_text('discovery_id = "x"\n')
    with pytest.raises(ConfigError, match="invalid discovery spec"):
        load_discovery_spec(incomplete)


def _spec_dict() -> dict:  # type: ignore[type-arg]
    return load_discovery_spec(D1).model_dump(mode="json")


def test_empty_and_duplicate_parameters_rejected() -> None:
    d = _spec_dict()
    d["parameters"] = []
    with pytest.raises(ValidationError, match="at least one"):
        DiscoverySpec.model_validate(d)
    d = _spec_dict()
    d["parameters"] = d["parameters"][:1] * 2
    with pytest.raises(ValidationError, match="unique"):
        DiscoverySpec.model_validate(d)


def test_model_ref_must_be_exact_and_id_must_be_clean() -> None:
    d = _spec_dict()
    d["model_ref"] = "efficiency_rebound@latest"
    with pytest.raises(ValidationError, match="exact"):
        DiscoverySpec.model_validate(d)
    d = _spec_dict()
    d["discovery_id"] = "has space"
    with pytest.raises(ValidationError):
        DiscoverySpec.model_validate(d)


@pytest.mark.parametrize(
    "kw",
    [
        {"low": 1.0},  # only one bound
        {"low": 2.0, "high": 1.0},
        {"low": 1.0, "high": 1.0},
        {"low": float("nan"), "high": 1.0},
        {"low": 0.0, "high": 1.0, "grid_points": 2},
        {"low": 0.0, "high": 1.0, "xtol": 0.0},
        {"low": 0.0, "high": 1.0, "provenance_class": "sourced"},  # evidence-backed ranges not supported yet
        {"low": 0.0, "high": 1.0, "provenance_class": "measured"},
        {"low": 0.0, "high": 1.0, "provenance_class": "illustrative"},  # marker missing in basis
    ],
)
def test_invalid_parameter_ranges(kw: dict) -> None:  # type: ignore[type-arg]
    with pytest.raises(ValidationError):
        ParameterRange(variable_id="epsilon", basis="b", **kw)


def test_basis_is_mandatory_and_nonblank() -> None:
    with pytest.raises(ValidationError):
        ParameterRange(variable_id="epsilon", low=0.0, high=1.0)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        ParameterRange(variable_id="epsilon", low=0.0, high=1.0, basis="  ")


def test_illustrative_range_with_marker_is_valid() -> None:
    ParameterRange(variable_id="epsilon", low=0.0, high=1.0, basis=MARK, provenance_class="illustrative")


def test_weights_validation() -> None:
    ScoringWeights(proximity=0.5, magnitude=0.25, evidence=0.25)
    with pytest.raises(ValidationError, match="sum to 1"):
        ScoringWeights(proximity=0.5, magnitude=0.5, evidence=0.5)
    with pytest.raises(ValidationError, match="non-negative"):
        ScoringWeights(proximity=-0.1, magnitude=0.6, evidence=0.5)


# ------------------------------------------------------------------ step 2: regions and sensitivity contracts
from ecobackfire.contracts.discovery import RegionSearch, SensitivitySpec  # noqa: E402

D2 = ROOT / "examples" / "discovery" / "d2_regions_sensitivity.toml"


def _pr(var: str, **kw: object) -> ParameterRange:
    return ParameterRange(variable_id=var, basis="b", **kw)  # type: ignore[arg-type]


def test_d2_example_loads_with_explicit_seed() -> None:
    spec = load_discovery_spec(D2)
    assert spec.parameters == [] and len(spec.regions) == 2
    assert spec.sensitivity is not None and spec.sensitivity.seed == 12345
    assert spec.sensitivity.target.variable_id == "epsilon"
    assert all(b.provenance_class.value == "illustrative" for b in spec.bindings.values())


def test_region_needs_two_different_variables() -> None:
    with pytest.raises(ValidationError, match="two different"):
        RegionSearch(x=_pr("phi"), y=_pr("phi"))


def test_sensitivity_seed_is_mandatory_and_validated() -> None:
    kw = dict(target=_pr("epsilon"), parameters=[_pr("phi")])
    with pytest.raises(ValidationError):
        SensitivitySpec(**kw)  # type: ignore[arg-type]  # no seed => refused
    with pytest.raises(ValidationError, match="non-negative"):
        SensitivitySpec(seed=-1, **kw)  # type: ignore[arg-type]
    for bad in (3, 15):
        with pytest.raises(ValidationError, match="n_samples_log2"):
            SensitivitySpec(seed=1, n_samples_log2=bad, **kw)  # type: ignore[arg-type]
    SensitivitySpec(seed=0, **kw)  # type: ignore[arg-type]


def test_sensitivity_parameter_rules() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        SensitivitySpec(target=_pr("epsilon"), parameters=[], seed=1)
    with pytest.raises(ValidationError, match="unique"):
        SensitivitySpec(target=_pr("epsilon"), parameters=[_pr("phi"), _pr("phi")], seed=1)
    with pytest.raises(ValidationError, match="target variable"):
        SensitivitySpec(target=_pr("epsilon"), parameters=[_pr("epsilon")], seed=1)


def test_spec_needs_parameters_or_regions() -> None:
    d = _spec_dict()
    d["parameters"], d["regions"] = [], []
    with pytest.raises(ValidationError, match="at least one"):
        DiscoverySpec.model_validate(d)
    d["regions"] = load_discovery_spec(D2).model_dump(mode="json")["regions"]
    DiscoverySpec.model_validate(d)  # regions-only is fine
