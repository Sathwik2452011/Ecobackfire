"""Discovery -> candidate -> evidence-sensitive evaluation through the REAL model (SYNTHETIC evidence registries).

Needs the full stack (pint, pytest). All evidence here is synthetic (dataset 'synthetic-resolution'); the values are
not environmental data. The model is the real efficiency_rebound@1.0.0 evaluated through experiments.runner.make_g.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _resolution_fixtures import DS, VER, build_registry, f_record, request

from ecobackfire import api
from ecobackfire.contracts.discovery import DiscoverySpec
from ecobackfire.discovery import scoring
from ecobackfire.errors import ConfigError, EvidenceConflictError
from ecobackfire.reporting.evidence_manifest import reproduce_evidence_evaluation
from ecobackfire.verification.reference import rebound_analytical as ana

pytestmark = pytest.mark.software
EID = f"{DS}@{VER}:f.in.2024"


def _spec(e2_config, **req):  # type: ignore[no-untyped-def]
    return DiscoverySpec.model_validate({
        "discovery_id": "t_evidence_sensitive", "title": "t", "research_question": "t",
        "model_ref": e2_config.model_ref, "options": dict(e2_config.options),
        "decision_metric_ref": e2_config.decision_metric_ref,
        "bindings": {k: v.model_dump(mode="json") for k, v in e2_config.bindings.items() if k != "f"},
        "evidence_requests": {"f": request(**req)},
        "parameters": [{"variable_id": "epsilon", "low": 0.0, "high": 3.0,
                        "basis": "ILLUSTRATIVE — not evidence: demonstration range",
                        "provenance_class": "illustrative"}],
    })


def test_documented_range_is_evaluated_through_the_real_model(e2_config, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    reg = build_registry(tmp_path / "r", [f_record(uncertainty={"kind": "interval", "low": 600.0, "high": 800.0})])
    run_ = api.evaluate_discovery_evidence(_spec(e2_config), reg)
    out = run_.outcome
    assert len(out.evaluations) == 1
    ev = out.evaluations[0]
    roles = {o.role: o for o in ev.outcomes_for("f")}
    assert set(roles) == {"lower", "central", "upper"} and roles["central"].is_baseline
    assert (roles["lower"].value, roles["upper"].value) == (pytest.approx(0.6), pytest.approx(0.8))
    assert all(o.dataset_id == DS and o.dataset_version == VER and o.evidence_id == EID for o in ev.outcomes)
    assert ev.decision.classification in scoring.ROBUSTNESS_TIER and ev.deterministic
    assert (
        out.ranking == [ev.candidate_id]
        and out.axes[ev.candidate_id]["scientific_validity"] == scoring.VALIDITY_NOT_ASSESSED
    )
    assert "### Evidence basis" in (run_.report_markdown or "")


def test_single_point_record_gives_insufficient_evidence(e2_config, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    reg = build_registry(tmp_path / "r", [f_record()])
    ev = api.evaluate_discovery_evidence(_spec(e2_config), reg).outcome.evaluations[0]
    assert ev.decision.evidence_class == "evidence_insufficient"
    assert ev.decision.classification != "robust"  # never robust without documented variation


def test_conflict_needs_an_explicit_baseline_then_every_record_is_an_alternative(e2_config, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    reg = build_registry(tmp_path / "r", [f_record(), f_record(record_id="f.in.2024.b", value=300.0)])
    with pytest.raises(EvidenceConflictError):
        api.evaluate_discovery_evidence(_spec(e2_config), reg)  # never silently choose a source
    out = api.evaluate_discovery_evidence(_spec(e2_config, select=EID), reg)
    ev = out.outcome.evaluations[0]
    assert {o.evidence_id for o in ev.outcomes_for("f")} == {EID, f"{DS}@{VER}:f.in.2024.b"}
    assert ev.scenario_sets[0].baseline_evidence_id == EID
    assert ev.decision.classification != "robust" or all(o.preserved for o in ev.outcomes)


def test_manifest_reproduces_and_results_are_identical_on_rerun(e2_config, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    reg = build_registry(tmp_path / "r", [f_record(uncertainty={"kind": "interval", "low": 600.0, "high": 800.0})])
    a = api.evaluate_discovery_evidence(_spec(e2_config), reg)
    b = api.evaluate_discovery_evidence(_spec(e2_config), reg)
    assert a.manifest is not None and a.manifest.model_dump_json() == b.manifest.model_dump_json()  # type: ignore[union-attr]
    (tmp_path / "o").mkdir()
    api.evaluate_discovery_evidence(_spec(e2_config), reg, out_dir=tmp_path / "o")
    assert (tmp_path / "o" / "evidence_sensitivity.json").exists() and (tmp_path / "o" / "evidence_report.md").exists()
    assert a.report_markdown == b.report_markdown
    out = reproduce_evidence_evaluation(
        a.manifest, lambda: api.evaluate_discovery_evidence(_spec(e2_config), reg).outcome.evaluations
    )
    assert out.ok and out.exact


def test_no_evidence_requests_is_a_configuration_error(e2_config, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    d = _spec(e2_config).model_dump(mode="json")
    d.pop("evidence_requests")
    with pytest.raises(ConfigError):
        api.evaluate_discovery_evidence(DiscoverySpec.model_validate(d), build_registry(tmp_path / "r", [f_record()]))


def test_scenario_roots_agree_with_the_independent_closed_form(e2_config, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """Every evidence scenario is solved by the real model; the independent closed form must give the same root."""
    reg = build_registry(tmp_path / "r", [f_record(uncertainty={"kind": "interval", "low": 600.0, "high": 800.0})])
    ev = api.evaluate_discovery_evidence(_spec(e2_config), reg).outcome.evaluations[0]
    inputs = {k: b.value for k, b in e2_config.bindings.items()}
    for o in ev.outcomes_for("f"):
        want = ana.predicted_epsilon_roots({**inputs, "f": o.value}, dict(e2_config.options))
        assert o.root == pytest.approx(want[0], rel=1e-9), o.scenario_id
