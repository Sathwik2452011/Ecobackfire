"""Mission disclosure: evidence summary, settings disclosure/audit, verification disclosure, budget usage.

Canonical-mission tests use the REAL registry (data/releases). Variants with documented ranges use SYNTHETIC
registries (dataset 'synthetic-resolution'; values are not environmental data). No real second source exists, and
none is invented here.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
from _resolution_fixtures import DS, VER, build_registry, f_record

from ecobackfire.contracts.mission import MissionResult, ResearchMission
from ecobackfire.datasets.registry import DatasetRegistry
from ecobackfire.mission import runner as mr
from ecobackfire.mission import summary as sm
from ecobackfire.mission.loader import load_mission
from ecobackfire.mission.planner import plan_mission

pytestmark = pytest.mark.software

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "missions" / "example.toml"
REAL = ROOT / "data" / "releases"


@pytest.fixture(scope="module")
def real_registry() -> DatasetRegistry:
    return DatasetRegistry.from_directory(REAL)


@pytest.fixture(scope="module")
def canonical_run(real_registry: DatasetRegistry, tmp_path_factory: pytest.TempPathFactory) -> mr.MissionRun:
    return mr.run_mission(
        load_mission(EXAMPLE), tmp_path_factory.mktemp("disc"), real_registry, source_path=str(EXAMPLE)
    )


def _mdict() -> dict[str, Any]:
    return tomllib.loads(EXAMPLE.read_text(encoding="utf-8"))


def _synthetic(**req: Any) -> ResearchMission:
    d = _mdict()
    d["evidence_requirements"] = {"f": {"dataset_id": DS, "version": VER, **req}}
    return ResearchMission.model_validate(d)


# ---------------------------------------------------------------- 1. evidence summary


def test_canonical_evidence_summary_counts_are_computed_from_the_evaluations(canonical_run: mr.MissionRun) -> None:
    r = canonical_run.result
    es = r.evidence_summary
    assert es is not None
    n_eval = len(r.classification)
    assert es.total_candidates == len(r.candidates) and es.candidates_evaluated == n_eval
    n_insuff = sum(1 for c in r.classification if c.decision and c.decision.evidence_class == "evidence_insufficient")
    assert es.evidence_insufficient == n_insuff
    # the real f has one value and no range/alternative: no evaluated candidate has documented variation
    assert es.candidates_with_evidence_variation == 0 and es.evidence_insufficient == n_eval
    assert es.source_dependent == 0 and es.evidence_robust == 0
    assert sum(es.by_evidence_class.values()) == n_eval
    assert "0 of" in es.statement and "'completed' means the pipeline ran" in es.statement
    f = next(a for a in es.evidence_available if a.variable_id == "f")
    assert f.state == "resolved" and not f.has_documented_variation
    assert "eta" in es.inputs_without_registry_evidence


def test_documented_range_is_counted_as_evidence_variation(tmp_path: Path) -> None:
    reg = build_registry(tmp_path / "r", [f_record(uncertainty={"kind": "interval", "low": 600.0, "high": 800.0})])
    r = mr.run_mission(_synthetic(), tmp_path / "o", reg).result
    es = r.evidence_summary
    assert es is not None and es.candidates_evaluated >= 1
    assert es.candidates_with_evidence_variation == es.candidates_evaluated - es.evidence_insufficient
    assert es.candidates_with_evidence_variation >= 1
    # the candidate whose searched variable IS the evidence-documented input (f) cannot be challenged on it
    insufficient = [
        c.candidate_id for c in r.classification if c.decision and c.decision.evidence_class == "evidence_insufficient"
    ]
    assert insufficient == ["f#0"]
    f = next(a for a in es.evidence_available if a.variable_id == "f")
    assert f.n_documented_ranges == 1 and f.has_documented_variation


def test_evidence_summary_is_propagated_to_result_file_and_report(canonical_run: mr.MissionRun) -> None:
    d = canonical_run.run_dir
    assert d is not None
    again = MissionResult.model_validate_json((d / "mission_result.json").read_text(encoding="utf-8"))
    assert again.evidence_summary == canonical_run.result.evidence_summary
    report = (d / "mission_report.md").read_text(encoding="utf-8")
    assert canonical_run.result.evidence_summary.statement in report  # type: ignore[union-attr]
    assert "candidates_with_evidence_variation: 0" in report


# ---------------------------------------------------------------- 2. settings disclosure


def test_canonical_settings_are_disclosed_exactly(canonical_run: mr.MissionRun) -> None:
    a = canonical_run.result.analysis_settings
    assert a is not None and a.applied and a.perturbation_scheme == "one_at_a_time"
    assert a.falsification["perturbation_levels"] == [0.01, 0.05, 0.1] and a.falsification["repeats"] == 3
    assert a.falsification["agree_tol"] == 1e-8
    assert a.robustness["repeats"] == 2 and a.robustness["agree_tol"] == 1e-8
    by = {p["variable_id"]: p for p in a.robustness_perturbations_applied}
    assert set(by) == {"eta", "phi"} == set(a.robustness_perturbed_parameters)
    assert by["eta"]["relative_half_width_percent"] == pytest.approx(10.0)
    assert by["phi"]["relative_half_width_percent"] == pytest.approx(5.0)
    assert all(p["mode"] == "one_at_a_time" for p in a.robustness_perturbations_applied)
    assert "NOT a broad robustness analysis" in a.scope_statement and "ONE input at a time" in a.scope_statement
    assert a.audit.status == "no_weakening_vs_project_defaults"


def test_settings_are_recorded_per_candidate_by_the_engine(canonical_run: mr.MissionRun) -> None:
    r = canonical_run.result
    assert r.falsification[0].settings and r.robustness[0].settings
    assert {p["name"] for p in r.robustness[0].settings["perturbations"]} == {"eta", "phi"}  # type: ignore[index,union-attr]


# ------------------------------------------------ 3. weak-setting audit (warnings, never silent rejection)


def test_weak_robustness_settings_produce_warnings_not_refusals(tmp_path: Path, real_registry: DatasetRegistry) -> None:
    d = _mdict()
    d["falsification"].update({"perturbation_levels": [0.01], "agree_tol": 1e-4, "edge_margin": 0.005})
    d["robustness"]["agree_tol"] = 1e-4
    d["robustness"]["perturbations"] = []
    r = mr.run_mission(ResearchMission.model_validate(d), tmp_path, real_registry).result
    assert r.status == "completed"  # not rejected
    a = r.analysis_settings
    assert a is not None and a.audit.status == "warnings"
    codes = {f.code for f in a.audit.findings}
    assert {
        "falsification_weaker_max_perturbation",
        "falsification_fewer_levels",
        "falsification_agree_tol_more_permissive",
        "falsification_edge_margin_weaker",
        "robustness_agree_tol_more_permissive",
        "robustness_no_perturbation_applied",
    } <= codes
    assert any(w.startswith("settings audit:") for w in r.warnings)


def test_repeats_below_two_are_refused_by_the_existing_validators_and_audited(
    tmp_path: Path, real_registry: DatasetRegistry
) -> None:
    d = _mdict()
    d["falsification"]["repeats"] = 1
    r = mr.run_mission(ResearchMission.model_validate(d), tmp_path, real_registry).result
    assert r.status == "invalid" and r.reason == "invalid_mission"  # existing minimum (>= 2), not a new threshold
    assert r.analysis_settings is not None and not r.analysis_settings.applied  # declared, never applied
    assert "falsification_repeats_below_2" in {f.code for f in r.analysis_settings.audit.findings}


def test_audit_core_flags_only_deviations_from_project_defaults() -> None:
    fd = {
        "repeats": 3,
        "perturbation_levels": [0.01, 0.05, 0.1],
        "agree_tol": 1e-8,
        "noise_tol": 1e-6,
        "edge_margin": 0.02,
        "range_expansion": 0.25,
    }
    rd = {"repeats": 2, "agree_tol": 1e-8, "edge_margin": 0.02}
    pert = [{"variable_id": "eta", "relative_half_width": 0.1}]
    ok = sm.audit_settings(fd, rd, pert, [], fd, rd)
    assert ok["status"] == "no_weakening_vs_project_defaults" and ok["n_warnings"] == 0
    weak = sm.audit_settings(
        {**fd, "repeats": 1, "range_expansion": 0.1}, {**rd, "repeats": 1}, [], ["x skipped"], fd, rd
    )
    codes = {f["code"] for f in weak["findings"]}
    assert {"falsification_repeats_below_2", "robustness_repeats_below_2", "falsification_range_expansion_weaker",
            "robustness_no_perturbation_applied", "robustness_perturbation_skipped"} <= codes


# ---------------------------------------------------------------- 4. verification disclosure


def test_verification_disclosure_names_exactly_the_verified_candidate(canonical_run: mr.MissionRun) -> None:
    r = canonical_run.result
    v = r.verification_disclosure
    assert v is not None and v.performed
    assert v.verified_candidate_ids == [r.candidates[0].candidate.candidate_id]
    assert v.verification_label == r.provenance["experiment_run"]["verification_label"]  # type: ignore[index]
    others = [c.candidate.candidate_id for c in r.candidates[1:] if c.evaluated]
    assert v.falsified_and_robustness_tested_not_verified == others
    assert "not independently verified with the full standard verification suite" in v.statement or not others


def test_verification_disclosure_core_wording() -> None:
    ranked = [
        {"candidate_id": "a", "evaluated": True},
        {"candidate_id": "b", "evaluated": True},
        {"candidate_id": "c", "evaluated": False},
    ]
    d = sm.verification_disclosure(ranked, "a", "conditional", True)
    assert d["verified_candidate_ids"] == ["a"] and d["falsified_and_robustness_tested_not_verified"] == ["b"]
    assert d["discovery_only_candidate_ids"] == ["c"] and "not independently verified" in d["statement"]
    n = sm.verification_disclosure(ranked, None, None, False)
    assert n["performed"] is False and n["verified_candidate_ids"] == []


# ---------------------------------------------------------------- 5. budget


def test_budget_usage_reports_actual_evaluations_against_the_cap(
    canonical_run: mr.MissionRun, real_registry: DatasetRegistry
) -> None:
    b = canonical_run.result.budget_usage
    assert b is not None
    assert 0 < (b.actual_model_evaluations or 0) <= b.configured_max_model_evaluations
    assert b.budget_reached is False
    assert b.remaining_model_evaluations == b.configured_max_model_evaluations - (b.actual_model_evaluations or 0)
    assert (b.actual_model_evaluations or 0) <= (b.estimated_upper_bound_at_plan_time or 0)
    assert b.actual_model_evaluations_reproducibility_rerun == b.actual_model_evaluations  # deterministic
    assert "not a measurement" in b.estimate_note and "UPPER-BOUND ESTIMATE" in b.estimate_note
    po = plan_mission(load_mission(EXAMPLE), real_registry)
    assert b.estimated_upper_bound_at_plan_time == po.plan.budget.estimated_model_evaluations  # type: ignore[union-attr]


def test_budget_usage_core_flags_a_reached_budget() -> None:
    hit = sm.budget_usage(100, 500, 100, None, [3], 500, 1, 10)
    assert hit["budget_reached"] is True and hit["remaining_model_evaluations"] == 0
    none = sm.budget_usage(100, 500, None, None, [], 500, 0, 10)
    assert none["budget_reached"] is None and "not counted" in none["statement"]


def test_evaluation_counter_restores_the_model_class() -> None:
    class M:
        def evaluate(self, x: int) -> int:
            return x + 1

    orig = M.evaluate
    m = M()
    with sm.count_model_evaluations(m) as n:
        m.evaluate(1)
        m.evaluate(2)
    assert n() == 2 and M.evaluate is orig
