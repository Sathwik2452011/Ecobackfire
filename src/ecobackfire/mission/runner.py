"""Investigation runner: execute an InvestigationPlan with the EXISTING components and return a MissionResult.

    validate -> resolve evidence -> (prepare inputs) -> discovery -> candidate evaluation
             -> falsification -> robustness -> classification -> artifacts -> reproducibility

Every stage either succeeds or yields an explicit, structured ``MissionFailure``; nothing is defaulted to a
passing/robust outcome. All scientific work is delegated:

* evidence      ``datasets.resolution`` via the planner
* discovery     ``discovery.engine.run_discovery``
* falsification / robustness / classification   ``discovery.evidence_discovery`` (X1-X7, R1, evidence
                challenges, categorical rules) through ``api.evaluate_discovery_evidence``
* ranking       ``discovery.scoring`` (robustness tier first, then the existing transparent attention score)
* experiment run + 12-check verification + report + manifest   the existing ``api`` run-directory writer
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ecobackfire import api
from ecobackfire.contracts.discovery import Candidate, DiscoveryResult
from ecobackfire.contracts.evidence_sensitivity import LIMITATIONS, CandidateEvidenceEvaluation
from ecobackfire.contracts.experiment import ExperimentConfiguration, SearchSpec
from ecobackfire.contracts.mission import (
    AnalysisSettings,
    AssumptionRecord,
    BudgetUsage,
    ClassificationView,
    EvidenceSummary,
    FailureCode,
    FalsificationSettings,
    FalsificationView,
    InvestigationPlan,
    MissionFailure,
    MissionReproducibility,
    MissionResult,
    MissionStatus,
    ModelSummary,
    RankedCandidate,
    ResearchMission,
    RobustnessSettings,
    RobustnessView,
    StageRecord,
    VerificationDisclosure,
)
from ecobackfire.core.hashing import canonical_json, canonicalize, pretty_json, sha256_bytes, sha256_text
from ecobackfire.datasets.registry import DatasetRegistry
from ecobackfire.discovery.engine import run_discovery
from ecobackfire.errors import EcoBackfireError
from ecobackfire.experiments.evidence import materialize_config
from ecobackfire.experiments.runner import run_experiment
from ecobackfire.mission import summary as sm
from ecobackfire.mission.backfire import build_backfire_summary
from ecobackfire.mission.planner import (
    STAGES,
    PlanOutcome,
    build_perturbations,
    evidence_sensitivity_config,
    falsification_config,
    mission_sha256,
    plan_mission,
    plan_sha256,
    robustness_config,
)
from ecobackfire.mission.report import render_mission_report
from ecobackfire.reporting.manifest import DEPENDENCIES, TOLERANCE, build_manifest, dependency_versions, json_close
from ecobackfire.verification.suite import verify as _verify

RegistryLike = DatasetRegistry | str | Path | None

MISSION_LIMITATIONS: tuple[str, ...] = (
    "Candidates are model-derived hypotheses (break-even locations of the declared model under the declared "
    "inputs), never empirical findings.",
    "The ranking score orders attention; it is not a probability that a candidate is true and it never merges "
    "with evidence documentation or robustness, which are reported separately.",
    "Inputs without registry evidence are assumptions or illustrative placeholders; every candidate is conditional "
    "on them.",
    "Perturbation ranges configured in the mission are sensitivity experiments, not uncertainty distributions.",
    "Falsification attempts can only refute what they can see: sub-grid sign changes and even-multiplicity roots "
    "remain invisible to the scan.",
)


@dataclass
class PipelineOutcome:
    """Everything the scientific pipeline produced (no files, no clock): the unit that is run twice for the
    reproducibility check."""

    discovery: DiscoveryResult | None = None
    evaluations: dict[str, CandidateEvidenceEvaluation] = field(default_factory=dict)
    ranked: list[RankedCandidate] = field(default_factory=list)
    falsification: list[FalsificationView] = field(default_factory=list)
    robustness: list[RobustnessView] = field(default_factory=list)
    classification: list[ClassificationView] = field(default_factory=list)
    evidence_manifest_json: str | None = None
    evidence_report_md: str | None = None
    stages: list[StageRecord] = field(default_factory=list)
    failure: MissionFailure | None = None
    status: MissionStatus = "completed"
    reason: FailureCode | None = None
    warnings: list[str] = field(default_factory=list)
    # disclosure only (not part of the scientific payload): the robustness perturbations actually built
    perturbations: list[object] = field(default_factory=list)
    perturbation_notes: list[str] = field(default_factory=list)
    analysis_applied: bool = False

    def scientific_payload(self) -> dict[str, object]:
        """The part that must be equivalent between two executions of the same mission."""
        return canonicalize(
            {
                "status": self.status,
                "reason": self.reason,
                "candidates": [c.model_dump(mode="json") for c in self.ranked],
                "falsification": [v.model_dump(mode="json") for v in self.falsification],
                "robustness": [v.model_dump(mode="json") for v in self.robustness],
                "classification": [v.model_dump(mode="json") for v in self.classification],
                "rejected": []
                if self.discovery is None
                else [r.model_dump(mode="json") for r in self.discovery.rejected],
            }
        )  # type: ignore[no-any-return]


def _stage(stages: list[StageRecord], name: str, status: str, detail: str = "") -> None:
    stages.append(StageRecord(stage=name, status=status, detail=detail))  # type: ignore[arg-type]


def _fail(
    out: PipelineOutcome,
    stage: str,
    code: FailureCode,
    message: str,
    details: list[str] | None = None,
    status: MissionStatus = "failed",
) -> PipelineOutcome:
    out.failure = MissionFailure(stage=stage, code=code, message=message, details=details or [])
    out.status = status
    out.reason = code
    _stage(out.stages, stage, "failed", message)
    return out


def _claim_type(disc: DiscoveryResult) -> str:
    if disc.weakest_input_provenance.value == "illustrative":
        return "model_derived_illustrative"
    return "model_derived_conditional"


def _not_reached(out: PipelineOutcome, from_stage: str) -> None:
    seen = {s.stage for s in out.stages}
    started = False
    for st in STAGES:
        if st == from_stage:
            started = True
        if started and st not in seen:
            _stage(out.stages, st, "not_reached")


def run_pipeline(mission: ResearchMission, po: PlanOutcome, registry: DatasetRegistry) -> PipelineOutcome:
    """discovery -> candidate evaluation (falsification, robustness, classification) -> ranking. Pure of I/O."""
    out = PipelineOutcome()
    assert po.spec is not None and po.model is not None  # plan.status == "ready"
    for st in ("validate_mission", "resolve_evidence", "prepare_model_inputs"):
        _stage(out.stages, st, "ok")

    # ---- discovery (existing engine; evidence is re-resolved from the same registry, exactly as planned)
    try:
        disc = run_discovery(po.spec, po.model, registry=registry)
    except EcoBackfireError as exc:
        _fail(out, "discovery", "discovery_failed", f"{type(exc).__name__}: {exc}")
        _not_reached(out, "candidate_evaluation")
        return out
    out.discovery = disc
    out.warnings += disc.warnings
    _stage(
        out.stages, "discovery", "ok", f"{len(disc.candidates)} candidate(s), {len(disc.rejected)} rejected search(es)"
    )
    if not disc.candidates:
        out.status = "completed_no_candidates"
        out.reason = "no_candidates"
        out.failure = None
        _stage(out.stages, "candidate_evaluation", "skipped", "discovery produced no candidate; nothing to falsify")
        for st in ("falsification", "robustness", "classification"):
            _stage(out.stages, st, "skipped", "no candidate")
        return out

    # backfire hunt: EVERY discovered candidate is falsified (bounded only by the mission's max_candidates)
    cap = (
        mission.budget.max_candidates
        if mission.backfire is not None
        else min(mission.discovery.top_k, mission.budget.max_candidates)
    )
    chosen = [c.candidate_id for c in sorted(disc.candidates, key=lambda c: c.rank)[:cap]]
    _stage(out.stages, "candidate_evaluation", "ok", f"top {len(chosen)} of {len(disc.candidates)} by discovery rank")

    # ---- falsification + robustness + classification (existing evidence-sensitive evaluation)
    base = {k: (q.value, q.unit) for k, q in disc.base_inputs.items()}
    try:
        perts, notes = build_perturbations(mission, base)
        out.warnings += notes
        out.perturbations, out.perturbation_notes = list(perts), list(notes)
        run = api.evaluate_discovery_evidence(
            po.spec,
            registry,
            None,
            model=po.model,
            config=evidence_sensitivity_config(mission),
            falsification_config=falsification_config(mission),
            robustness_perturbations=perts,
            robustness_config=robustness_config(mission),
            discovery_result=disc,
            candidate_ids=chosen,
        )
    except EcoBackfireError as exc:
        _fail(
            out,
            "falsification",
            "falsification_failed",
            f"falsification/robustness evaluation failed: {type(exc).__name__}: {exc}",
            ["candidates are reported from discovery only and are NOT classified; none may be read as robust"],
        )
        out.ranked = _unevaluated_ranking(disc, po, chosen=[])
        _not_reached(out, "robustness")
        return out
    evals = {e.candidate_id: e for e in run.outcome.evaluations}
    out.evaluations = evals
    out.analysis_applied = True
    out.evidence_manifest_json = None if run.manifest is None else pretty_json(run.manifest.model_dump(mode="json"))
    out.evidence_report_md = run.report_markdown

    # ---- views (propagation, not interpretation)
    for cid in chosen:
        e = evals.get(cid)
        if e is None:
            continue
        fals = [c for c in e.challenges if c.challenge_type == "numerical_falsification"]
        rob = [c for c in e.challenges if c.challenge_type != "numerical_falsification"]
        out.falsification.append(
            FalsificationView(
                candidate_id=cid,
                status="assessed" if fals else "not_assessed",
                checks=fals,
                settings=dict(e.falsification_settings),
            )
        )
        out.robustness.append(
            RobustnessView(
                candidate_id=cid,
                status="assessed" if e.dimensions else "not_assessed",
                checks=rob,
                dimensions=list(e.dimensions),
                deterministic=e.deterministic,
                settings=dict(e.robustness_settings),
            )
        )
        out.classification.append(ClassificationView(candidate_id=cid, decision=e.decision))
        if not e.deterministic:
            out.warnings.append(f"{cid}: the evaluation was not bit-for-bit deterministic across its internal repeats")
    missing = [cid for cid in chosen if cid not in evals]
    if missing:
        return _fail(
            out,
            "falsification",
            "falsification_failed",
            f"no evaluation was produced for candidate(s) {missing}",
            ["those candidates are unclassified and must not be read as robust"],
        )
    n_f = sum(1 for v in out.falsification if v.status == "assessed")
    n_r = sum(1 for v in out.robustness if v.status == "assessed")
    _stage(out.stages, "falsification", "ok", f"{n_f}/{len(chosen)} candidate(s) assessed")
    _stage(out.stages, "robustness", "ok", f"{n_r}/{len(chosen)} candidate(s) assessed")
    _stage(out.stages, "classification", "ok", f"{len(out.classification)} decision(s)")

    # ---- ranking: the existing evidence-aware order for evaluated candidates, then the rest by discovery rank
    by_id = {c.candidate_id: c for c in disc.candidates}
    order = list(run.outcome.ranking) + [
        c.candidate_id for c in sorted(disc.candidates, key=lambda c: c.rank) if c.candidate_id not in evals
    ]
    ctype = _claim_type(disc)
    for i, cid in enumerate(order, start=1):
        cand = by_id[cid]
        e = evals.get(cid)
        out.ranked.append(
            RankedCandidate(
                rank=i,
                candidate=cand,
                ranking_score=cand.score.total,
                ranking_note="attention-ordering score from the existing transparent formula; not a probability",
                score_axes=run.outcome.axes.get(cid, {}),
                robustness_classification=None if e is None else e.decision.classification,
                claim_type=ctype if e is not None else "not_assessed",  # type: ignore[arg-type]
                evaluated=e is not None,
            )
        )
    return out


def _unevaluated_ranking(disc: DiscoveryResult, po: PlanOutcome, chosen: list[str]) -> list[RankedCandidate]:
    del po, chosen
    return [
        RankedCandidate(
            rank=i,
            candidate=c,
            ranking_score=c.score.total,
            ranking_note="discovery-stage attention score only; falsification did not complete",
            score_axes={},
            robustness_classification=None,
            claim_type="not_assessed",
            evaluated=False,
        )
        for i, c in enumerate(sorted(disc.candidates, key=lambda c: c.rank), start=1)
    ]


# ---------------------------------------------------------------- experiment run (existing artifact format)


def _experiment_config(mission: ResearchMission, po: PlanOutcome, cand: Candidate) -> ExperimentConfiguration:
    assert po.spec is not None
    pr = next(p for p in po.spec.parameters if p.variable_id == cand.variable_id)
    return ExperimentConfiguration(
        experiment_id=f"{mission.mission_id}__{cand.variable_id}",
        title=f"{mission.title} - break-even of {cand.variable_id}",
        research_question=mission.research_question,
        model_ref=mission.model_ref,
        kind="breakeven",
        options=dict(mission.options),
        bindings=dict(po.spec.bindings),
        evidence_requests=dict(po.spec.evidence_requests),
        search=SearchSpec(
            variable_id=cand.variable_id,
            low=cand.search_low,
            high=cand.search_high,
            grid_points=cand.grid_points,
            xtol=pr.xtol,
        ),
        decision_metric_ref=mission.target_metric_ref,
    )


def _write(path: Path, text: str) -> str:
    data = text.encode("utf-8")
    path.write_bytes(data)
    return sha256_bytes(data)


def _assumptions(plan: InvestigationPlan, po: PlanOutcome, mission: ResearchMission) -> list[AssumptionRecord]:
    out: list[AssumptionRecord] = []
    for pi in plan.inputs:
        if pi.role not in ("assumed", "illustrative"):
            continue
        b = mission.bindings[pi.variable_id]
        kind = "fallback_for_missing_evidence" if pi.variable_id in mission.evidence_requirements else pi.role
        out.append(
            AssumptionRecord(
                variable_id=pi.variable_id,
                provenance_class=b.provenance_class.value,
                value=b.value,
                unit=b.unit,
                note=b.note or "",
                kind=kind,  # type: ignore[arg-type]
            )
        )
    return out


def _scientific_sentence(out: PipelineOutcome, mission: ResearchMission) -> str:
    if out.status == "completed_no_candidates":
        return (
            "Discovery found no continuous break-even crossing in the declared search ranges; the rejected searches "
            "and their reasons are listed. This is a model-derived outcome, not a statement about a real system."
        )
    if not out.ranked:
        return "No scientific result was produced."
    top = out.ranked[0]
    cls = top.robustness_classification or "not classified"
    return (
        f"Top-ranked model-derived candidate '{top.candidate.candidate_id}' (classification: {cls}; claim type: "
        f"{top.claim_type}). {top.candidate.hypothesis} "
        + (
            "Inputs without registry evidence are illustrative or assumed, so the result is conditional on them and "
            "says nothing about a real product or system. "
            if top.claim_type != "not_assessed"
            else ""
        )
        + "The ranking score is an attention ordering, not a probability."
    )


def _analysis_settings(mission: ResearchMission, out: PipelineOutcome) -> AnalysisSettings:
    """The settings actually used (the engine configs the planner builds), described with an audit against defaults."""
    try:
        fal = asdict(falsification_config(mission))
        rob_cfg = asdict(robustness_config(mission))
        ev_cfg = evidence_sensitivity_config(mission).model_dump(mode="json")
    except (EcoBackfireError, ValueError):
        # an invalid configuration: show what was declared (the run itself failed or was refused)
        fal = mission.falsification.model_dump(mode="json")
        rob_cfg = {"agree_tol": mission.robustness.agree_tol, "repeats": mission.robustness.repeats}
        ev_cfg = {"edge_margin": mission.robustness.edge_margin, "repeats": mission.robustness.repeats}
    fal = canonicalize(fal)
    rob = {"edge_margin": mission.robustness.edge_margin, **canonicalize(rob_cfg)}
    pert = sm.effective_perturbations(out.perturbations)
    d = sm.settings_disclosure(
        fal, rob, pert, out.perturbation_notes, canonicalize(ev_cfg), out.analysis_applied  # type: ignore[arg-type]
    )
    d["audit"] = sm.audit_settings(
        fal,  # type: ignore[arg-type]
        rob,
        pert,
        out.perturbation_notes,
        FalsificationSettings().model_dump(mode="json"),
        RobustnessSettings().model_dump(mode="json"),
    )
    return AnalysisSettings.model_validate(d)


def _evidence_summary(po: PlanOutcome, out: PipelineOutcome, mission: ResearchMission) -> EvidenceSummary | None:
    if out.discovery is None:
        return None
    assumed = [a.variable_id for a in _assumptions(po.plan, po, mission)] if po.plan.status == "ready" else []
    d = sm.evidence_summary(
        len(out.discovery.candidates), list(out.evaluations.values()), po.plan.evidence, assumed
    )
    return EvidenceSummary.model_validate(d)


def _budget_usage(
    po: PlanOutcome, out: PipelineOutcome, mission: ResearchMission, counts: tuple[int | None, int | None]
) -> BudgetUsage:
    b = mission.budget
    est = None if po.plan.budget is None else po.plan.budget.estimated_model_evaluations
    scen = [len(e.outcomes) for e in out.evaluations.values()]
    d = sm.budget_usage(
        b.max_model_evaluations,
        est,
        counts[0],
        counts[1],
        scen,
        b.max_scenarios,
        len(out.evaluations),
        b.max_candidates,
    )
    return BudgetUsage.model_validate(d)


def _verification_disclosure(
    out: PipelineOutcome, verified_id: str | None, label: str | None, performed: bool
) -> VerificationDisclosure:
    ranked = [{"candidate_id": c.candidate.candidate_id, "evaluated": c.evaluated} for c in out.ranked]
    return VerificationDisclosure.model_validate(sm.verification_disclosure(ranked, verified_id, label, performed))


def _assemble(
    mission: ResearchMission,
    po: PlanOutcome,
    out: PipelineOutcome,
    status: MissionStatus | None = None,
    repro: MissionReproducibility | None = None,
    artifacts: dict[str, str] | None = None,
    provenance: dict[str, object] | None = None,
    source_path: str | None = None,
    counts: tuple[int | None, int | None] = (None, None),
) -> MissionResult:
    plan = po.plan
    disc = out.discovery
    model = None
    if po.model is not None:
        m = po.model
        opts = dict(plan.options)
        model = ModelSummary(
            model_ref=m.spec.ref,
            options=opts,
            system_boundary_id=m.system_boundary(opts).id,
            functional_unit=str(m.spec.assumptions.functional_unit.model_dump(mode="json")),
            decision_metric_ref=mission.target_metric_ref,
            target_environmental_metric=mission.target_environmental_metric,
        )
    summary = None if disc is None else disc.evidence_resolution
    sha = mission_sha256(mission)
    cmd = f"ecobackfire mission {source_path}" if source_path else "ecobackfire mission <mission.toml>"
    if repro is None:
        repro = MissionReproducibility(
            mission_sha256=sha,
            model_ref=plan.model_ref,
            reproduce_command=cmd,
            plan_sha256=plan_sha256(plan),
            discovery_spec_sha256=plan.discovery_spec_sha256,
        )
    return MissionResult(
        mission=mission,
        status=status or out.status,
        reason=out.reason,
        failure=out.failure,
        stages=out.stages,
        model=model,
        plan=plan,
        evidence_resolution=summary,
        evidence_summary=_evidence_summary(po, out, mission),
        analysis_settings=_analysis_settings(mission, out),
        verification_disclosure=_verification_disclosure(out, None, None, False),
        budget_usage=_budget_usage(po, out, mission, counts),
        backfire=None
        if disc is None or po.plan.status != "ready"
        else build_backfire_summary(
            plan, disc, out.ranked, out.falsification, out.robustness, out.classification,
            list(disc.backfire.limitations) if disc.backfire is not None else [],
        ),
        evidence_items=plan.evidence,
        assumptions=_assumptions(plan, po, mission) if plan.status == "ready" else [],
        candidates=out.ranked,
        rejected_searches=[] if disc is None else list(disc.rejected),
        falsification=out.falsification,
        robustness=out.robustness,
        classification=out.classification,
        scientific_result=_scientific_sentence(out, mission)
        if out.ranked or out.status == "completed_no_candidates"
        else "",
        provenance=provenance or {},
        artifacts=artifacts or {},
        reproducibility=repro,
        warnings=[*out.warnings, *_audit_warnings(mission, out)],
        limitations=[*MISSION_LIMITATIONS, *LIMITATIONS],
    )


def _audit_warnings(mission: ResearchMission, out: PipelineOutcome) -> list[str]:
    """Surface the settings-audit warnings in the top-level warnings (only when the analysis stages ran)."""
    if not out.analysis_applied:
        return []
    audit = _analysis_settings(mission, out).audit
    return [
        f"settings audit: {f.message}"
        for f in audit.findings
        if f.severity == "warning" and f.code != "robustness_perturbation_skipped"  # already in warnings
    ]


def _repro_base(mission: ResearchMission, po: PlanOutcome, out: PipelineOutcome, cmd: str) -> MissionReproducibility:
    disc = out.discovery
    summ = None if disc is None else disc.evidence_resolution
    payload_hash = sha256_text(canonical_json(out.scientific_payload())) if out.ranked or out.discovery else None
    sens = mission.discovery.sensitivity
    return MissionReproducibility(
        mission_sha256=mission_sha256(mission),
        plan_sha256=plan_sha256(po.plan),
        discovery_spec_sha256=po.plan.discovery_spec_sha256,
        evidence_resolution_sha256=None if summ is None else sha256_text(canonical_json(summ.model_dump(mode="json"))),
        evidence_registry_sha256=None if summ is None else summ.registry_sha256,
        datasets=[]
        if summ is None
        else [f"{r.dataset_id}@{r.version} content_sha256={r.content_sha256}" for r in summ.releases_used],
        model_ref=po.plan.model_ref,
        seed=None,
        sensitivity_seed=None if sens is None else sens.seed,
        dependencies=dependency_versions(),
        scientific_results_sha256=payload_hash,
        reproduce_command=cmd,
    )


def _compare(a: PipelineOutcome, b: PipelineOutcome) -> tuple[str, list[str]]:
    pa, pb = a.scientific_payload(), b.scientific_payload()
    if canonical_json(pa) == canonical_json(pb):
        return "reproduced_exactly", []
    diffs = json_close(pa, pb, TOLERANCE["rtol"], TOLERANCE["atol"])
    if not diffs:
        return "reproduced_within_tolerance", [
            "scientific payload bytes differ but every number agrees within tolerance"
        ]
    return "not_reproduced", diffs[:8]


@dataclass
class MissionRun:
    result: MissionResult
    run_dir: Path | None


def _load_registry(registry: RegistryLike) -> tuple[DatasetRegistry | None, str | None]:
    if registry is None or isinstance(registry, DatasetRegistry):
        return registry, None
    try:
        return DatasetRegistry.from_directory(registry, strict=True), None
    except EcoBackfireError as exc:
        return None, f"dataset registry could not be loaded: {type(exc).__name__}: {exc}"


def run_mission(
    mission: ResearchMission,
    out_root: str | Path = "runs",
    registry: RegistryLike = None,
    *,
    source_path: str | None = None,
    write: bool = True,
) -> MissionRun:
    """Plan and execute a mission. The registry is loaded ONCE and shared by every stage. With ``write`` the run
    directory ``<out_root>/<mission_id>-<hash8>/`` receives the standard experiment artifacts (config, result,
    verification, report, manifest) for the best candidate plus the mission artifacts."""
    sha = mission_sha256(mission)
    run_dir = Path(out_root) / f"{mission.mission_id}-{sha[:8]}"
    cmd = f"ecobackfire mission {source_path}" if source_path else "ecobackfire mission <mission.toml>"
    reg, reg_error = _load_registry(registry)
    po = plan_mission(mission, reg)
    plan = po.plan

    def finish_early(
        status: MissionStatus, reason: FailureCode, stage: str, message: str, details: list[str]
    ) -> MissionRun:
        out = PipelineOutcome(status=status, reason=reason)
        out.stages.append(
            StageRecord(
                stage="validate_mission",
                status="ok" if stage != "validate_mission" else "failed",
                detail=message if stage == "validate_mission" else "",
            )
        )
        if stage != "validate_mission":
            out.stages.append(StageRecord(stage=stage, status="failed", detail=message))
        out.failure = MissionFailure(stage=stage, code=reason, message=message, details=details)
        _not_reached(out, "discovery")
        res = _assemble(
            mission,
            po,
            out,
            repro=MissionReproducibility(
                mission_sha256=sha, plan_sha256=plan_sha256(plan), model_ref=plan.model_ref, reproduce_command=cmd
            ),
        )
        return _write_mission_dir(mission, po, res, None, run_dir, source_path, write, cmd)

    if reg_error is not None:
        return finish_early("failed", "missing_evidence", "resolve_evidence", reg_error, [])
    if plan.status == "invalid":
        return finish_early("invalid", "invalid_mission", "validate_mission", "the mission is not valid", plan.problems)
    if plan.status == "unresolved":
        assert plan.reason is not None
        return finish_early(
            "unresolved",
            plan.reason,
            "resolve_evidence",
            "evidence is missing or ambiguous; no value was invented",
            plan.problems,
        )
    if plan.status == "over_budget":
        return finish_early(
            "failed",
            "budget_exceeded",
            "validate_mission",
            "the planned investigation exceeds the mission's computational budget",
            plan.problems,
        )
    assert reg is not None

    assert po.model is not None
    with sm.count_model_evaluations(po.model) as n_first:
        out = run_pipeline(mission, po, reg)
    n_rerun: int | None = None
    repro = _repro_base(mission, po, out, cmd)
    if out.failure is None and mission.reproducibility_check:
        with sm.count_model_evaluations(po.model) as n_second:
            second = run_pipeline(mission, po, reg)
        n_rerun = n_second()
        outcome, details = _compare(out, second)
        repro = repro.model_copy(update={"check_performed": True, "check_outcome": outcome, "check_details": details})
        if outcome == "not_reproduced":
            out = _fail(
                out,
                "reproducibility",
                "reproducibility_failed",
                "a second execution of the identical mission did not reproduce the scientific results",
                details,
            )
        else:
            _stage(out.stages, "reproducibility", "ok", outcome)
    elif out.failure is None:
        _stage(out.stages, "reproducibility", "skipped", "reproducibility_check = false in the mission")
    res = _assemble(mission, po, out, repro=repro, counts=(n_first(), n_rerun))
    return _write_mission_dir(mission, po, res, out, run_dir, source_path, write, cmd, reg)


def _write_mission_dir(
    mission: ResearchMission,
    po: PlanOutcome,
    res: MissionResult,
    out: PipelineOutcome | None,
    run_dir: Path,
    source_path: str | None,
    write: bool,
    cmd: str,
    reg: DatasetRegistry | None = None,
) -> MissionRun:
    if not write:
        return MissionRun(res, None)
    run_dir.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    hashes["mission.json"] = _write(run_dir / "mission.json", pretty_json(mission.model_dump(mode="json")))
    hashes["plan.json"] = _write(run_dir / "plan.json", pretty_json(po.plan.model_dump(mode="json")))
    prov: dict[str, object] = {}
    verified_id: str | None = None
    verified_label: str | None = None
    failure = res.failure
    status = res.status
    reason = res.reason
    stages = list(res.stages)
    if out is not None and out.discovery is not None:
        hashes["discovery.json"] = _write(
            run_dir / "discovery.json", pretty_json(out.discovery.model_dump(mode="json"))
        )
    if out is not None and out.evidence_manifest_json is not None and out.evidence_report_md is not None:
        hashes["evidence_sensitivity.json"] = _write(run_dir / "evidence_sensitivity.json", out.evidence_manifest_json)
        hashes["evidence_report.md"] = _write(run_dir / "evidence_report.md", out.evidence_report_md)
    # standard experiment run (config/result/verification/report/manifest) for the best candidate
    if out is not None and out.ranked and out.failure is None and reg is not None:
        top = out.ranked[0].candidate
        try:
            cfg = _experiment_config(mission, po, top)
            effective, mat = materialize_config(cfg, reg)
            exp_result = run_experiment(effective, registry=reg)
            verification = _verify(effective, exp_result, verified_evidence=mat.verified_evidence)
            api.write_artifacts(
                run_dir, effective, exp_result, verification, source_path,
                f"ecobackfire mission {source_path}" if source_path else "ecobackfire mission <mission.toml>",
            )
            for name in ("config.json", "result.json", "verification.json", "report.md"):
                hashes[name] = sha256_bytes((run_dir / name).read_bytes())
            verified_id, verified_label = top.candidate_id, verification.recommended_label
            prov["experiment_run"] = {
                "experiment_id": effective.experiment_id,
                "config_sha256": exp_result.config_sha256,
                "verification_label": verification.recommended_label,
                "candidate_id": top.candidate_id,
                "note": "standard EcoBackfire run directory artifacts (config/result/verification/report/manifest) "
                "for the top-ranked candidate; reproduce with `ecobackfire reproduce manifest.json`",
            }
        except EcoBackfireError as exc:
            failure = MissionFailure(
                stage="artifacts",
                code="artifact_failed",
                message=f"the standard experiment run/verification failed: {type(exc).__name__}: {exc}",
                details=["the candidate is not independently verified by the 12-check suite"],
            )
            status, reason = "failed", "artifact_failed"
            stages.append(StageRecord(stage="artifacts", status="failed", detail=failure.message))
    if failure is None or failure.stage != "artifacts":
        stages = [s for s in stages if s.stage != "artifacts"] + [
            StageRecord(stage="artifacts", status="ok", detail=f"{len(hashes)} file(s) written")
        ]
    prov["mission_sha256"] = res.reproducibility.mission_sha256
    prov["datasets"] = res.reproducibility.datasets
    prov["model_ref"] = res.plan.model_ref if res.plan else mission.model_ref
    update: dict[str, object] = {
        "status": status,
        "reason": reason,
        "failure": failure,
        "stages": stages,
        "provenance": prov,
    }
    if out is not None:
        update["verification_disclosure"] = _verification_disclosure(
            out, verified_id, None if verified_label is None else str(verified_label), verified_id is not None
        )
    final = res.model_copy(update=update)
    hashes["mission_report.md"] = _write(run_dir / "mission_report.md", render_mission_report(final))
    final = final.model_copy(update={"artifacts": dict(sorted(hashes.items()))})
    result_hash = _write(run_dir / "mission_result.json", pretty_json(final.model_dump(mode="json")))
    all_hashes = {**hashes, "mission_result.json": result_hash}
    manifest = build_manifest(
        evidence=final.evidence_resolution,
        run_id=run_dir.name,
        config_sha256=final.reproducibility.mission_sha256,
        model_ref=po.plan.model_ref,
        artifact_hashes=all_hashes,
        source_config_path=source_path,
        reproduce_command=cmd,
    )
    manifest = manifest.model_copy(update={"config_file": "mission.json"})
    (run_dir / "mission_manifest.json").write_text(pretty_json(manifest.model_dump(mode="json")), encoding="utf-8")
    return MissionRun(final, run_dir)


@dataclass
class MissionReproduceOutcome:
    ok: bool
    exact: bool
    details: list[str] = field(default_factory=list)


def reproduce_mission(manifest_path: str | Path, registry: RegistryLike = None) -> MissionReproduceOutcome:
    """Re-run the mission stored next to ``mission_manifest.json`` and compare every artifact hash it lists."""
    mp = Path(manifest_path)
    run_dir = mp.parent
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    mission = ResearchMission.model_validate_json((run_dir / manifest["config_file"]).read_text(encoding="utf-8"))
    if mission_sha256(mission) != manifest["config_sha256"]:
        return MissionReproduceOutcome(False, False, ["mission.json does not match the manifest's mission hash"])
    tampered = [
        n
        for n, h in manifest["artifact_hashes"].items()
        if not (run_dir / n).exists() or sha256_bytes((run_dir / n).read_bytes()) != h
    ]
    if tampered:
        return MissionReproduceOutcome(False, False, [f"{n}: stored artifact is missing or modified" for n in tampered])
    with tempfile.TemporaryDirectory() as tmp:
        run = run_mission(mission, tmp, registry, source_path=manifest.get("source_config_path"))
        assert run.run_dir is not None
        exact, ok, details = True, True, []
        for name, want in manifest["artifact_hashes"].items():
            got = (run.run_dir / name).read_bytes()
            if sha256_bytes(got) == want:
                continue
            exact = False
            if name.endswith(".json"):
                d = json_close(
                    json.loads((run_dir / name).read_bytes()), json.loads(got), TOLERANCE["rtol"], TOLERANCE["atol"]
                )
                if d:
                    ok = False
                    details.append(f"{name}: differs beyond tolerance: {d[:3]}")
                else:
                    details.append(f"{name}: bytes differ but numerically equal within tolerance")
            else:
                ok = False
                details.append(f"{name}: content hash mismatch")
        for dep, ver in manifest.get("dependencies", {}).items():
            now = dependency_versions().get(dep)
            if now != ver and dep in DEPENDENCIES:
                details.append(f"dependency {dep}: recorded {ver}, now {now} (platform/library drift)")
        return MissionReproduceOutcome(ok, exact and ok, details)
