"""Research-mission orchestration: contract, planner, runner, artifacts, determinism, structured failures.

The canonical mission runs against the REAL registry (data/releases). Variants that need ranges, conflicts or other
evidence states use SYNTHETIC registries (dataset 'synthetic-resolution'; values are not environmental data).
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from _resolution_fixtures import DS, VER, build_registry, f_record

from ecobackfire import api
from ecobackfire.cli.main import main as cli_main
from ecobackfire.contracts.mission import ResearchMission
from ecobackfire.core.hashing import sha256_bytes
from ecobackfire.datasets.registry import DatasetRegistry
from ecobackfire.errors import ConfigError, NumericalError
from ecobackfire.mission import runner as mr
from ecobackfire.mission.loader import load_mission
from ecobackfire.mission.planner import estimate_evaluations, mission_sha256, plan_mission
from ecobackfire.verification.reference import rebound_analytical as ana

pytestmark = pytest.mark.software

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "missions" / "example.toml"
REAL = ROOT / "data" / "releases"


def mdict(**over: Any) -> dict[str, Any]:
    """The canonical mission WITHOUT its [backfire] block: these tests cover the classic (top_k) pipeline; the
    automated hunt is covered in test_backfire_mission.py."""
    d = tomllib.loads(EXAMPLE.read_text(encoding="utf-8"))
    d.pop("backfire", None)
    d.update(over)
    return d


def mission_from(d: dict[str, Any]) -> ResearchMission:
    return ResearchMission.model_validate(d)


@pytest.fixture(scope="module")
def real_registry() -> DatasetRegistry:
    return DatasetRegistry.from_directory(REAL)


@pytest.fixture(scope="module")
def canonical_run(real_registry: DatasetRegistry, tmp_path_factory: pytest.TempPathFactory) -> mr.MissionRun:
    return mr.run_mission(
        load_mission(EXAMPLE), tmp_path_factory.mktemp("canon"), real_registry, source_path=str(EXAMPLE)
    )


def _synthetic_mission(**req: Any) -> dict[str, Any]:
    d = mdict()
    d["evidence_requirements"] = {"f": {"dataset_id": DS, "version": VER, **req}}
    return d


# ------------------------------------------------------------------ 1. mission validation


def test_canonical_mission_loads_and_hash_is_stable() -> None:
    a, b = load_mission(EXAMPLE), load_mission(EXAMPLE)
    assert a.mission_id == "m1_rebound_breakeven_india" and mission_sha256(a) == mission_sha256(b)


@pytest.mark.parametrize(
    "mutate, text",
    [
        (lambda d: d.update(search=[]), "at least one search parameter"),
        (lambda d: d.update(evidence_requirements={}), "at least one evidence requirement"),
        (lambda d: d.update(acknowledges_illustrative_inputs=False), "illustrative"),
        (lambda d: d.update(model_ref="efficiency_rebound@latest"), "no 'latest'"),
        (lambda d: d.update(mission_id="has space"), "mission_id"),
        (lambda d: d.update(research_question="  "), "research_question"),
        (lambda d: d.update(search=[d["search"][0], d["search"][0]]), "unique"),
        (lambda d: d["evidence_requirements"]["f"].update(required=False), "fallback binding"),
        (lambda d: d["budget"].update(max_candidates=0), "must be >= 1"),
        (lambda d: d["discovery"].update(top_k=0), "top_k"),
        (lambda d: d.update(mystery_field=1), "Extra inputs"),
    ],
)
def test_invalid_missions_are_rejected_by_the_contract(mutate: Any, text: str) -> None:
    d = mdict()
    mutate(d)
    with pytest.raises(Exception, match=text):
        mission_from(d)


def test_loader_reports_config_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_mission(tmp_path / "nope.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("mission_id = ", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_mission(bad)
    bad.write_text('mission_id = "x"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid mission"):
        load_mission(bad)


def test_planner_marks_semantically_invalid_missions_invalid(real_registry: DatasetRegistry) -> None:
    d = mdict()
    d["search"] = [*d["search"], {"variable_id": "no_such_input", "low": 0.0, "high": 1.0, "basis": "x"}]
    plan = plan_mission(mission_from(d), real_registry).plan
    assert plan.status == "invalid" and plan.reason == "invalid_mission" and "no_such_input" in " ".join(plan.problems)
    d = mdict(target_metric_ref="not_a_metric")
    assert plan_mission(mission_from(d), real_registry).plan.status == "invalid"
    d = mdict()
    d["falsification"] = {"edge_margin": 0.9}
    p = plan_mission(mission_from(d), real_registry).plan
    assert p.status == "invalid" and "falsification" in " ".join(p.problems)
    d = mdict()
    d["search"] = [{"variable_id": "phi", "basis": "x"}]
    d["bindings"]["phi"]["uncertainty"] = {"kind": "unknown"}
    assert plan_mission(mission_from(d), real_registry).plan.status == "invalid"  # ranges are never invented


# ------------------------------------------------------------------ 2. planning and 3. evidence resolution


def test_canonical_plan_identifies_evidence_assumptions_dimensions_and_budget(real_registry: DatasetRegistry) -> None:
    po = plan_mission(mission_from(mdict()), real_registry)
    p = po.plan
    assert p.status == "ready" and p.reason is None and po.spec is not None
    assert p.resolved_evidence == ["f"] and p.missing_evidence == [] and p.conflicting_evidence == []
    ev = next(i for i in p.evidence if i.variable_id == "f")
    assert (
        ev.state == "resolved"
        and ev.evidence_id
        and ev.evidence_id.startswith("owid-electricity-carbon-intensity@1.0.0:")
    )
    roles = {i.variable_id: i.role for i in p.inputs}
    assert roles["f"] == "evidence" and roles["E0"] == roles["eta"] == roles["EM1"] == "illustrative"
    assert roles["s_max"] == "unused_under_options"
    assert any("E0" in a and "illustrative" in a for a in p.assumptions)
    assert [d.variable_id for d in p.search_dimensions] == ["epsilon", "f", "EM1", "L"]
    assert p.search_dimensions[2].range_source == "binding_interval"
    assert p.budget is not None and p.budget.within_budget and p.budget.estimated_model_evaluations > 0
    assert p.stages[0] == "validate_mission" and p.stages[-1] == "reproducibility"
    # the effective spec: the resolved input comes from the request, not from a binding
    assert "f" not in po.spec.bindings and "f" in po.spec.evidence_requests
    again = plan_mission(mission_from(mdict()), real_registry).plan
    assert again.model_dump_json() == p.model_dump_json()  # deterministic


def test_missing_required_evidence_is_unresolved_not_invented(real_registry: DatasetRegistry, tmp_path: Path) -> None:
    d = mdict()
    d["scope"] = {"geography": "BR", "period": "2024"}  # the release has no Brazil record
    m = mission_from(d)
    plan = plan_mission(m, real_registry).plan
    assert plan.status == "unresolved" and plan.reason == "missing_evidence" and plan.missing_evidence == ["f"]
    run = mr.run_mission(m, tmp_path, real_registry)
    r = run.result
    assert (r.status, r.reason) == ("unresolved", "missing_evidence")
    assert r.failure is not None and r.failure.code == "missing_evidence" and r.candidates == []
    assert r.classification == [] and r.falsification == [] and r.robustness == []
    assert {s.stage: s.status for s in r.stages}["discovery"] == "not_reached"
    assert run.run_dir is not None and (run.run_dir / "mission_result.json").exists()  # the failure is an artifact
    assert not (run.run_dir / "result.json").exists()  # no experiment was run on invented data


def test_unknown_dataset_version_is_missing_evidence(real_registry: DatasetRegistry) -> None:
    d = mdict()
    d["evidence_requirements"]["f"]["version"] = "9.9.9"
    plan = plan_mission(mission_from(d), real_registry).plan
    assert plan.status == "unresolved" and plan.reason == "missing_evidence"


def test_missing_registry_is_unresolved_not_a_crash(tmp_path: Path) -> None:
    r = mr.run_mission(load_mission(EXAMPLE), tmp_path, None).result
    assert (r.status, r.reason) == ("unresolved", "missing_evidence")
    r = mr.run_mission(load_mission(EXAMPLE), tmp_path, tmp_path / "does-not-exist").result
    assert r.status == "failed" and r.failure is not None and "registry" in r.failure.message


def test_input_with_neither_binding_nor_evidence_is_missing(real_registry: DatasetRegistry) -> None:
    d = mdict()
    del d["bindings"]["E0"]
    plan = plan_mission(mission_from(d), real_registry).plan
    assert plan.status == "unresolved" and "E0" in plan.missing_evidence


def test_optional_missing_evidence_falls_back_to_a_reported_assumption(tmp_path: Path) -> None:
    recs = [
        f_record(),  # f (IN, 2024)
        f_record(record_id="phi.in.2024", variable_id="phi", value=0.8, unit="dimensionless"),
    ]
    reg = build_registry(tmp_path / "r", recs)
    d = mdict()
    d["evidence_requirements"] = {
        "f": {"dataset_id": DS, "version": VER},
        "phi": {"dataset_id": DS, "version": VER},
        "eta": {
            "dataset_id": DS,
            "version": VER,
            "required": False,
        },  # no eta record: optional, binding is the fallback
    }
    plan = plan_mission(mission_from(d), reg).plan
    assert plan.status == "ready" and plan.resolved_evidence == ["f", "phi"] and plan.missing_evidence == ["eta"]
    eta = next(i for i in plan.inputs if i.variable_id == "eta")
    assert eta.role == "illustrative" and "fallback" in eta.detail
    assert any("eta" in a and "fallback" in a for a in plan.assumptions)
    # all optional + all missing: there is nothing documented to challenge candidates with -> unresolved
    d["evidence_requirements"] = {"eta": {"dataset_id": DS, "version": VER, "required": False}}
    assert plan_mission(mission_from(d), reg).plan.reason == "missing_evidence"


def test_conflicting_evidence_is_reported_with_alternatives_and_blocks(tmp_path: Path) -> None:
    reg = build_registry(tmp_path / "r", [f_record(), f_record(record_id="f.in.2024.b", value=300.0)])
    m = mission_from(_synthetic_mission())
    plan = plan_mission(m, reg).plan
    assert plan.status == "unresolved" and plan.reason == "conflicting_evidence" and plan.conflicting_evidence == ["f"]
    item = plan.evidence[0]
    assert item.state == "conflicting" and len(item.alternatives) == 2  # both alternatives preserved
    r = mr.run_mission(m, tmp_path / "o", reg).result
    assert (r.status, r.reason) == ("unresolved", "conflicting_evidence") and r.candidates == []
    assert len(r.evidence_items[0].alternatives) == 2


def test_pinned_conflict_runs_and_every_source_challenges_the_candidate(tmp_path: Path) -> None:
    reg = build_registry(tmp_path / "r", [f_record(), f_record(record_id="f.in.2024.b", value=300.0)])
    m = mission_from(_synthetic_mission(select=f"{DS}@{VER}:f.in.2024"))
    r = mr.run_mission(m, tmp_path / "o", reg).result
    assert r.status == "completed"
    top = next(rb for rb in r.robustness if rb.candidate_id == "epsilon#0")
    assert any(c.challenge_type == "source_disagreement" for c in top.checks)


# ------------------------------------------------------------------ 4. execution and propagation


def test_canonical_mission_executes_the_whole_chain(
    canonical_run: mr.MissionRun, real_registry: DatasetRegistry
) -> None:
    r = canonical_run.result
    assert r.status == "completed" and r.reason is None and r.failure is None
    assert {s.stage: s.status for s in r.stages} == {
        "validate_mission": "ok",
        "resolve_evidence": "ok",
        "prepare_model_inputs": "ok",
        "discovery": "ok",
        "candidate_evaluation": "ok",
        "falsification": "ok",
        "robustness": "ok",
        "classification": "ok",
        "reproducibility": "ok",
        "artifacts": "ok",
    }
    assert r.model is not None and r.model.model_ref == "efficiency_rebound@1.0.0"
    assert r.evidence_resolution is not None and r.evidence_resolution.n_evidence == 1
    assert len(r.assumptions) == 7 and all(a.kind == "illustrative" for a in r.assumptions)
    assert any(x.code == "no_sign_change_always_beneficial" for x in r.rejected_searches)  # rejected searches are kept


def test_candidate_matches_the_independent_closed_form(canonical_run: mr.MissionRun) -> None:
    r = canonical_run.result
    c = r.candidates[0]
    assert c.candidate.candidate_id == "epsilon#0" and c.rank == 1 and c.evaluated
    cfg = load_mission(EXAMPLE)
    inputs = {k: b.value for k, b in cfg.bindings.items()}
    inputs["f"] = 0.7054  # India 2024 from the real release (705.4 gCO2e/kWh)
    want = ana.predicted_epsilon_roots(inputs, dict(cfg.options))[0]
    assert c.candidate.value == pytest.approx(want, rel=1e-9)


def test_falsification_robustness_classification_are_propagated_per_candidate(canonical_run: mr.MissionRun) -> None:
    r = canonical_run.result
    f, rb, cl = r.falsification[0], r.robustness[0], r.classification[0]
    assert f.status == "assessed" and {c.check_id.split("_")[0] for c in f.checks} >= {"X1", "X2", "X3", "X4"}
    assert all(c.challenge_type == "numerical_falsification" for c in f.checks)
    assert rb.status == "assessed" and len(rb.dimensions) == 3 and rb.deterministic is True
    assert {c.challenge_type for c in rb.checks} <= {
        "parameter_perturbation",
        "evidence_range_sensitivity",
        "source_disagreement",
    }
    assert any(
        c.challenge_type == "parameter_perturbation" for c in rb.checks
    )  # X5 and the configured eta/phi perturbations
    assert any(c.check_id.startswith("X5") for c in rb.checks)
    assert any(c.check_id.startswith("R1") for c in rb.checks)  # the mission's configured eta/phi ranges were evaluated
    assert cl.decision is not None and cl.decision.classification == r.candidates[0].robustness_classification
    # one documented point value of f is never "robust": the evidence axis says so explicitly
    assert cl.decision.evidence_class == "evidence_insufficient" and cl.decision.classification != "robust"


def test_four_concepts_stay_separate_and_the_score_is_not_a_probability(canonical_run: mr.MissionRun) -> None:
    c = canonical_run.result.candidates[0]
    assert 0.0 <= c.ranking_score <= 1.0 and "not a probability" in c.ranking_note
    axes = c.score_axes
    assert set(axes) >= {"evidence_documentation", "scientific_validity", "candidate_robustness"}
    assert axes["scientific_validity"] == "not_assessed_by_scoring"  # validity is never inferred from the score
    assert c.claim_type == "model_derived_illustrative" and c.candidate.label == "candidate"
    text = canonical_run.result.scientific_result
    assert "not a probability" in text and "illustrative" in text.lower()


def test_run_directory_contains_the_existing_artifact_format(canonical_run: mr.MissionRun) -> None:
    d = canonical_run.run_dir
    assert d is not None
    for name in (
        "config.json",
        "result.json",
        "verification.json",
        "report.md",
        "manifest.json",
        "mission.json",
        "plan.json",
        "discovery.json",
        "evidence_sensitivity.json",
        "evidence_report.md",
        "mission_result.json",
        "mission_report.md",
        "mission_manifest.json",
    ):
        assert (d / name).exists(), name
    r = canonical_run.result
    for name, h in r.artifacts.items():  # recorded hashes match the files on disk
        assert sha256_bytes((d / name).read_bytes()) == h, name
    mm = json.loads((d / "mission_manifest.json").read_text(encoding="utf-8"))
    assert mm["config_file"] == "mission.json" and mm["config_sha256"] == mission_sha256(r.mission)
    assert mm["artifact_hashes"]["mission_result.json"] == sha256_bytes((d / "mission_result.json").read_bytes())
    assert mm["dataset_release_ref"] and "owid-electricity-carbon-intensity@1.0.0" in mm["dataset_release_ref"]
    assert (
        r.provenance["experiment_run"]["verification_label"] == "conditional"
    )  # existing 12-check suite on the top candidate
    from ecobackfire.core.hashing import pretty_json

    assert (d / "mission_result.json").read_text(encoding="utf-8") == pretty_json(r.model_dump(mode="json"))


def test_standard_run_artifacts_reproduce_with_the_existing_reproduce(canonical_run: mr.MissionRun) -> None:
    assert canonical_run.run_dir is not None
    out = api.reproduce(canonical_run.run_dir / "manifest.json", registry=REAL)
    assert out.ok and out.exact, out.details


def test_standard_run_inside_a_mission_names_the_mission_command_not_run(canonical_run: mr.MissionRun) -> None:
    """Regression: the standard-run manifest/report used to say `ecobackfire run <mission.toml>`, which cannot work."""
    import json

    assert canonical_run.run_dir is not None
    man = json.loads((canonical_run.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert man["reproduce_command"].startswith("ecobackfire mission ")
    assert "ecobackfire run" not in (canonical_run.run_dir / "report.md").read_text(encoding="utf-8")


def test_mission_result_roundtrips_through_its_own_contract(canonical_run: mr.MissionRun) -> None:
    from ecobackfire.contracts.mission import MissionResult

    d = canonical_run.run_dir
    assert d is not None
    again = MissionResult.model_validate_json((d / "mission_result.json").read_text(encoding="utf-8"))
    assert again.status == "completed" and again.candidates[0].candidate.candidate_id == "epsilon#0"
    assert again.reproducibility.check_outcome == "reproduced_exactly"
    assert {
        "mission",
        "status",
        "model",
        "evidence_resolution",
        "evidence_summary",
        "analysis_settings",
        "verification_disclosure",
        "budget_usage",
        "assumptions",
        "candidates",
        "falsification",
        "robustness",
        "classification",
        "provenance",
        "artifacts",
        "reproducibility",
        "limitations",
    } <= set(type(again).model_fields)


# ------------------------------------------------------------------ evidence ranges change the evidence axis


def test_documented_range_is_propagated_into_robustness(tmp_path: Path) -> None:
    reg = build_registry(tmp_path / "r", [f_record(uncertainty={"kind": "interval", "low": 600.0, "high": 800.0})])
    r = mr.run_mission(mission_from(_synthetic_mission()), tmp_path / "o", reg).result
    assert r.status == "completed"
    cl = r.classification[0].decision
    assert cl is not None and cl.evidence_class != "evidence_insufficient"
    rb = r.robustness[0]
    assert any(c.challenge_type == "evidence_range_sensitivity" for c in rb.checks)


# ------------------------------------------------------------------ failures are structured, never fake successes


def test_no_candidates_is_not_a_fake_success(real_registry: DatasetRegistry, tmp_path: Path) -> None:
    d = mdict()
    d["search"] = [
        {
            "variable_id": "epsilon",
            "low": 0.0,
            "high": 0.5,
            "provenance_class": "illustrative",
            "basis": "ILLUSTRATIVE — not evidence: range that excludes the break-even",
        }
    ]
    run = mr.run_mission(mission_from(d), tmp_path, real_registry)
    r = run.result
    assert (r.status, r.reason) == ("completed_no_candidates", "no_candidates")
    assert r.candidates == [] and r.classification == [] and r.rejected_searches
    assert "no continuous break-even" in r.scientific_result
    assert run.run_dir is not None and not (run.run_dir / "result.json").exists()


def test_discovery_failure_is_explicit(
    real_registry: DatasetRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise NumericalError("synthetic failure")

    monkeypatch.setattr(mr, "run_discovery", boom)
    r = mr.run_mission(load_mission(EXAMPLE), tmp_path, real_registry).result
    assert (r.status, r.reason) == ("failed", "discovery_failed")
    assert r.failure is not None and r.failure.stage == "discovery" and "synthetic failure" in r.failure.message
    assert r.candidates == [] and r.classification == []


def test_falsification_failure_never_classifies_a_candidate_as_robust(
    real_registry: DatasetRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise NumericalError("falsification exploded")

    monkeypatch.setattr(mr.api, "evaluate_discovery_evidence", boom)
    r = mr.run_mission(load_mission(EXAMPLE), tmp_path, real_registry).result
    assert (r.status, r.reason) == ("failed", "falsification_failed")
    assert r.candidates and all(c.robustness_classification is None and not c.evaluated for c in r.candidates)
    assert all(c.claim_type == "not_assessed" for c in r.candidates)
    assert r.classification == [] and r.falsification == []
    st = {s.stage: s.status for s in r.stages}
    assert st["falsification"] == "failed" and st["classification"] == "not_reached"


def test_reproducibility_failure_is_explicit(
    real_registry: DatasetRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = mr.run_pipeline
    calls = {"n": 0}

    def flaky(mission: Any, po: Any, reg: Any) -> Any:
        out = real(mission, po, reg)
        calls["n"] += 1
        if calls["n"] == 2:  # the second execution disagrees
            out.ranked[0] = out.ranked[0].model_copy(update={"ranking_score": out.ranked[0].ranking_score + 0.25})
        return out

    monkeypatch.setattr(mr, "run_pipeline", flaky)
    r = mr.run_mission(load_mission(EXAMPLE), tmp_path, real_registry).result
    assert (r.status, r.reason) == ("failed", "reproducibility_failed")
    assert r.reproducibility.check_outcome == "not_reproduced" and r.reproducibility.check_details
    assert r.failure is not None and r.failure.stage == "reproducibility"


def test_budget_is_enforced_before_anything_runs(real_registry: DatasetRegistry, tmp_path: Path) -> None:
    d = mdict()
    d["budget"]["max_model_evaluations"] = 1000
    run = mr.run_mission(mission_from(d), tmp_path, real_registry)
    r = run.result
    assert (r.status, r.reason) == ("failed", "budget_exceeded") and r.candidates == []
    assert r.plan is not None and not r.plan.budget.within_budget  # type: ignore[union-attr]
    d = mdict()
    d["budget"]["max_grid_points_per_parameter"] = 100
    assert plan_mission(mission_from(d), real_registry).plan.reason == "budget_exceeded"
    d = mdict()
    d["budget"]["max_search_parameters"] = 2
    assert plan_mission(mission_from(d), real_registry).plan.status == "over_budget"
    d = mdict()
    d["discovery"]["top_k"] = 50
    assert plan_mission(mission_from(d), real_registry).plan.status == "over_budget"  # top_k > max_candidates


def test_budget_estimate_is_a_true_upper_bound_for_the_canonical_mission(real_registry: DatasetRegistry) -> None:
    m = load_mission(EXAMPLE)
    po = plan_mission(m, real_registry)
    cls = type(po.model)
    orig = cls.evaluate
    n = [0]

    def counting(self: Any, *a: Any, **k: Any) -> Any:
        n[0] += 1
        return orig(self, *a, **k)

    cls.evaluate = counting  # type: ignore[method-assign]
    try:
        mr.run_pipeline(m, po, real_registry)
    finally:
        cls.evaluate = orig  # type: ignore[method-assign]
    assert 0 < n[0] <= po.plan.budget.estimated_model_evaluations  # type: ignore[union-attr]
    assert estimate_evaluations(m, 3, 3)[0] >= n[0]


# ------------------------------------------------------------------ determinism and reproducibility


def test_identical_missions_produce_identical_scientific_artifacts(
    real_registry: DatasetRegistry, tmp_path: Path, canonical_run: mr.MissionRun
) -> None:
    b = mr.run_mission(load_mission(EXAMPLE), tmp_path / "second", real_registry, source_path=str(EXAMPLE))
    a = canonical_run
    assert a.run_dir is not None and b.run_dir is not None and a.run_dir.name == b.run_dir.name
    assert a.result.model_dump_json() == b.result.model_dump_json()
    for name in a.result.artifacts:
        assert (a.run_dir / name).read_bytes() == (b.run_dir / name).read_bytes(), name


def test_reproducibility_record_has_what_is_needed_to_rerun(canonical_run: mr.MissionRun) -> None:
    rp = canonical_run.result.reproducibility
    assert rp.mission_sha256 and rp.plan_sha256 and rp.discovery_spec_sha256 and rp.scientific_results_sha256
    assert rp.evidence_registry_sha256 and rp.evidence_resolution_sha256 and rp.datasets
    assert rp.model_ref == "efficiency_rebound@1.0.0" and rp.seed is None  # no randomness is used
    assert {"numpy", "scipy", "pint", "pydantic"} <= set(rp.dependencies)
    assert rp.check_performed and rp.check_outcome == "reproduced_exactly"
    assert rp.reproduce_command.startswith("ecobackfire mission ")


def test_mission_reproduce_detects_tampering_and_confirms_clean_runs(
    canonical_run: mr.MissionRun, tmp_path: Path
) -> None:
    import shutil

    d = tmp_path / "copy"
    assert canonical_run.run_dir is not None
    shutil.copytree(canonical_run.run_dir, d)
    ok = mr.reproduce_mission(d / "mission_manifest.json", REAL)
    assert ok.ok and ok.exact, ok.details
    (d / "plan.json").write_text("{}", encoding="utf-8")
    bad = mr.reproduce_mission(d / "mission_manifest.json", REAL)
    assert not bad.ok and any("plan.json" in x for x in bad.details)


def test_changing_the_mission_changes_the_hash_and_run_directory(real_registry: DatasetRegistry) -> None:
    a = load_mission(EXAMPLE)
    d = mdict()
    d["falsification"] = {"edge_margin": 0.03}
    b = mission_from(d)
    assert mission_sha256(a) != mission_sha256(b)


# ------------------------------------------------------------------ CLI


def test_cli_runs_the_canonical_mission_and_reproduces_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["mission", str(EXAMPLE), "--out", str(tmp_path), "--registry", str(REAL)]) == 0
    out = capsys.readouterr().out
    assert "status: completed" in out and "epsilon#0" in out and "not a probability" in out
    run_dir = next(tmp_path.iterdir())
    assert cli_main(["mission-reproduce", str(run_dir / "mission_manifest.json"), "--registry", str(REAL)]) == 0
    assert cli_main(["reproduce", str(run_dir / "manifest.json"), "--registry", str(REAL)]) == 0


def test_cli_plan_only_and_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["mission", str(EXAMPLE), "--plan-only", "--registry", str(REAL)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"
    d = mdict()
    d["scope"] = {"geography": "BR", "period": "2024"}
    p = tmp_path / "m.toml"
    # TOML round trip is not available in the stdlib; write the one changed table by text substitution
    p.write_text(EXAMPLE.read_text(encoding="utf-8").replace('geography = "IN"', 'geography = "BR"'), encoding="utf-8")
    assert cli_main(["mission", str(p), "--out", str(tmp_path / "o"), "--registry", str(REAL)]) == 1
    assert "status: unresolved (reason: missing_evidence)" in capsys.readouterr().out
    assert cli_main(["mission", str(tmp_path / "missing.toml")]) == 2


# ------------------------------------------------------------------ 12. existing E2 regression


def test_e2_regression_is_untouched_by_the_orchestration_layer(tmp_path: Path) -> None:
    cfg = api.load_config(ROOT / "examples" / "rebound_breakeven" / "e2_breakeven_eps.toml")
    run_dir = api.execute(cfg, tmp_path, source_config_path="e2")
    assert run_dir.name == "e2_breakeven_eps-ec25be88"
    exp = ROOT / "examples" / "rebound_breakeven" / "expected" / "e2_breakeven_eps" / "result.json"
    assert (run_dir / "result.json").read_bytes() == exp.read_bytes()
    assert json.loads((run_dir / "verification.json").read_text())["recommended_label"] == "conditional"


# ------------------------------------------------------------------ unit handling of search bounds


def test_mission_bounds_are_canonical_even_when_the_evidence_record_uses_another_unit(
    canonical_run: mr.MissionRun, real_registry: DatasetRegistry
) -> None:
    """Regression: f is resolved in gCO2e/kWh; the mission range [0.01, 1.0] means kgCO2e/kWh (canonical)."""
    po = plan_mission(load_mission(EXAMPLE), real_registry)
    assert po.spec is not None
    f = next(p for p in po.spec.parameters if p.variable_id == "f")
    assert (f.low, f.high) == (pytest.approx(10.0), pytest.approx(1000.0))  # converted to the binding's unit
    cands = {c.candidate.variable_id: c.candidate for c in canonical_run.result.candidates}
    inputs = {k: b.value for k, b in load_mission(EXAMPLE).bindings.items()}
    inputs["f"] = 0.7054
    want = ana.predicted_f_roots(inputs, dict(load_mission(EXAMPLE).options))[0]
    assert cands["f"].value == pytest.approx(want, rel=1e-9)
    assert not any(x.variable_id == "f" for x in canonical_run.result.rejected_searches)


def test_candidates_outside_top_k_are_reported_but_not_assessed(real_registry: DatasetRegistry, tmp_path: Path) -> None:
    d = mdict()
    d["discovery"]["top_k"] = 1
    r = mr.run_mission(mission_from(d), tmp_path, real_registry).result
    assert [c.candidate.candidate_id for c in r.candidates] and len(r.candidates) == 2
    first, second = r.candidates
    assert first.evaluated and first.robustness_classification is not None
    assert not second.evaluated and second.robustness_classification is None and second.claim_type == "not_assessed"
    assert len(r.classification) == 1 and r.status == "completed"
