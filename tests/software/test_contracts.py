import pytest
from pydantic import ValidationError

from ecobackfire.contracts.common import (
    IntervalUncertainty,
    PointUncertainty,
    ProvenanceClass,
    SystemBoundary,
    UnknownUncertainty,
    weakest_provenance,
)
from ecobackfire.contracts.experiment import ExperimentConfiguration


def test_unknown_and_point_uncertainty_are_distinct():
    assert PointUncertainty().kind != UnknownUncertainty().kind


def test_interval_requires_ordering():
    with pytest.raises(ValueError):
        IntervalUncertainty(low=2.0, high=1.0)


def test_boundary_stages_cannot_be_both_included_and_excluded():
    with pytest.raises(ValueError):
        SystemBoundary(id="x", description="d", included_stages=["a"], excluded_stages=["a"])


def test_weakest_provenance_ordering():
    classes = [ProvenanceClass.MEASURED, ProvenanceClass.USER_SUPPLIED, ProvenanceClass.ILLUSTRATIVE]
    assert weakest_provenance(classes) is ProvenanceClass.ILLUSTRATIVE
    assert weakest_provenance([ProvenanceClass.SOURCED, ProvenanceClass.ESTIMATED]) is ProvenanceClass.ESTIMATED
    assert weakest_provenance([]) is None


def test_unknown_fields_are_rejected(e2_config):
    data = e2_config.model_dump(mode="json")
    data["surprise"] = 1
    with pytest.raises(ValidationError):
        ExperimentConfiguration.model_validate(data)


def test_breakeven_kind_requires_search(e2_config):
    data = e2_config.model_dump(mode="json")
    data["search"] = None
    with pytest.raises(ValidationError):
        ExperimentConfiguration.model_validate(data)


def test_configs_are_immutable(e2_config):
    with pytest.raises(ValidationError):
        e2_config.title = "changed"  # type: ignore[misc]


def test_run_on_a_research_mission_file_gives_an_actionable_hint():
    """Passing a Research Mission to the experiment loader must say so, not dump a raw validation error."""
    import pytest
    from conftest import EXAMPLES

    from ecobackfire import api
    from ecobackfire.errors import ConfigError

    mission = EXAMPLES.parent / "missions" / "example.toml"
    with pytest.raises(ConfigError) as ei:
        api.load_config(mission)
    msg = str(ei.value)
    assert "Research Mission" in msg and "ecobackfire mission" in msg
    assert "validation error" not in msg  # not the raw pydantic dump
