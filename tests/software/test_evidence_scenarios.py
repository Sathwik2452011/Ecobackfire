"""Evidence ranges, alternative sources, conflicts, missing evidence -> scenarios. SYNTHETIC fixtures only.
Pure: pydantic + stdlib (no pint, no pytest import)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _evidence_sensitivity_fixtures import UNIT, record, resolution

from ecobackfire.contracts.evidence_sensitivity import EvidenceRange, EvidenceScenarioSet
from ecobackfire.contracts.resolution import InputResolution
from ecobackfire.discovery.evidence_scenarios import build_scenario_set, record_range


def _raises(fn, *a, **k):
    try:
        fn(*a, **k)
    except Exception:
        return True
    return False


def test_documented_range_gives_lower_central_upper():
    r = record("a", 1.0, low=0.8, high=1.3)
    s = build_scenario_set(resolution("q", [r]), UNIT)
    assert [x.role for x in s.scenarios] == ["lower", "central", "upper"]
    assert [x.value for x in s.scenarios] == [0.8, 1.0, 1.3]
    assert len(s.ranges) == 1 and s.ranges[0].distribution_implied is False
    assert s.ranges[0].central == 1.0


def test_record_without_interval_gives_one_source_scenario_and_no_range():
    s = build_scenario_set(resolution("q", [record("a", 1.0)]), UNIT)
    assert [x.role for x in s.scenarios] == ["source"] and s.ranges == []


def test_no_range_is_invented_from_a_single_value():
    assert record_range(record("a", 1.0), "q", UNIT) is None


def test_range_contract_rejects_degenerate_and_outside_central():
    kw = dict(parameter="q", unit="1", evidence_id="e", dataset_id="d", dataset_version="1", provenance_class="sourced")
    assert _raises(EvidenceRange, lower=1.0, upper=1.0, **kw)
    assert _raises(EvidenceRange, lower=0.5, upper=1.0, central=2.0, **kw)
    assert _raises(EvidenceRange, lower=float("nan"), upper=1.0, **kw)


def test_alternatives_all_kept_and_baseline_marked():
    a, b = record("a", 1.0), record("b", 1.2)
    s = build_scenario_set(resolution("q", [a, b], select=a.evidence_id), UNIT)
    assert {x.evidence_id for x in s.scenarios} == {a.evidence_id, b.evidence_id}
    assert [x.is_baseline for x in s.scenarios] == [True, False]
    assert s.baseline_evidence_id == a.evidence_id and s.resolution_decision == "caller_selected"
    assert s.n_sources == 2


def test_conflicting_resolution_has_no_baseline_and_every_alternative():
    a, b = record("a", 1.0), record("b", 5.0)
    r = resolution("q", [a, b])
    assert r.state == "conflicting"
    s = build_scenario_set(r, UNIT)
    assert s.baseline_evidence_id is None and len(s.scenarios) == 2
    assert not any(x.is_baseline for x in s.scenarios)
    assert "no record was chosen" in s.resolution_decision


def test_missing_evidence_yields_no_scenarios_and_says_so():
    s = build_scenario_set(resolution("q", []), UNIT)
    assert s.resolution_state == "missing" and s.scenarios == [] and s.warnings
    assert _raises(EvidenceScenarioSet.model_validate, {**s.model_dump(), "scenarios": [
        build_scenario_set(resolution("q", [record("a", 1.0)]), UNIT).scenarios[0].model_dump()]})


def test_assumed_input_has_no_scenario_set():
    assert build_scenario_set(InputResolution(variable_id="q", basis="assumed"), UNIT) is None
    assert build_scenario_set(InputResolution(variable_id="q", basis="illustrative"), UNIT) is None


def test_provenance_fields_carried_into_every_scenario():
    r = record("a", 1.0, low=0.8, high=1.3, source="src-1", geography="G1", period="2024")
    for x in build_scenario_set(resolution("q", [r]), UNIT).scenarios:
        assert (x.dataset_id, x.dataset_version, x.source_id, x.source_key) == (
            r.dataset_id,
            r.version,
            "src-1",
            "src-1",
        )
        assert (x.geography, x.period, x.unit, x.provenance_class) == ("G1", "2024", UNIT, "sourced")
        assert x.release_content_sha256 == r.release_content_sha256 and x.evidence_id == r.evidence_id
        assert x.scenario_id == f"q|{r.evidence_id}|{x.role}"


def test_same_source_id_is_one_source():
    a, b = record("a", 1.0, source="same"), record("b", 1.1, source="same")
    assert build_scenario_set(resolution("q", [a, b], select=a.evidence_id), UNIT).n_sources == 1


def test_scenario_order_is_deterministic():
    a, b = record("a", 1.0), record("b", 1.2)
    s1 = build_scenario_set(resolution("q", [a, b], select=a.evidence_id), UNIT)
    s2 = build_scenario_set(resolution("q", [b, a], select=a.evidence_id), UNIT)
    assert s1.model_dump() == s2.model_dump()
