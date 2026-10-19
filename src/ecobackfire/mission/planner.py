"""Mission planner: ResearchMission -> InvestigationPlan (+ the effective DiscoverySpec it will run).

Deterministic and side-effect free. It resolves evidence (without ever raising for missing/conflicting records),
identifies resolved evidence, missing evidence, assumptions, search dimensions and the evaluation budget, and
builds the EFFECTIVE ``DiscoverySpec`` handed to the existing discovery engine. It performs no search itself.

Evidence rules (see ``MissionEvidenceRequirement``): required + missing/ambiguous -> the plan is ``unresolved``;
optional + missing -> the mission's own binding is used and listed as an assumption; conflicts are never resolved
silently (alternatives are preserved in the plan).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ecobackfire.contracts.common import IntervalUncertainty, ProvenanceClass
from ecobackfire.contracts.discovery import BackfireExclusion, DiscoverySpec, ParameterRange
from ecobackfire.contracts.evidence_sensitivity import EvidenceSensitivityConfig
from ecobackfire.contracts.experiment import Binding
from ecobackfire.contracts.mission import (
    BackfirePlan,
    BackfirePlannedParameter,
    EvaluationBudget,
    EvidenceItem,
    FailureCode,
    InvestigationPlan,
    PlannedInput,
    PlanStatus,
    ResearchMission,
    SearchDimension,
)
from ecobackfire.contracts.resolution import EvidenceRequest, InputResolution
from ecobackfire.core.hashing import canonical_json, sha256_text
from ecobackfire.core.model import Model
from ecobackfire.datasets.registry import DatasetRegistry
from ecobackfire.datasets.resolution import describe_unresolved, resolve_parameter
from ecobackfire.discovery.engine import spec_sha256
from ecobackfire.discovery.falsification import FalsificationConfig
from ecobackfire.discovery.robustness import PerturbationSpec, RobustnessConfig, relative_spec
from ecobackfire.errors import ConfigError, EcoBackfireError, MissingEvidenceError
from ecobackfire.experiments.runner import get_metric
from ecobackfire.models.registry import get_model
from ecobackfire.units import to_canonical

STAGES = [
    "validate_mission",
    "resolve_evidence",
    "prepare_model_inputs",
    "discovery",
    "candidate_evaluation",
    "falsification",
    "robustness",
    "classification",
    "artifacts",
    "reproducibility",
]

# Upper-bound cost model (model evaluations). These are ESTIMATES with deliberate head-room, calibrated against a
# counted run of the canonical mission (see tests/software/test_mission_budget.py); they are not measurements.
SEARCH_BRENT_EVALS = 100  # Brent refinement + bracketing per search
EVALS_PER_GRID_POINT_PER_CANDIDATE = 160  # falsification X1-X7 + robustness + evidence scenarios, repeated
EVALS_PER_PERTURBATION_PER_GRID_POINT = 2  # per configured perturbation value, per repeat
REGION_FACTOR = 3  # row scan + column scan + base evaluations per cell
SENSITIVITY_EVALS_PER_SAMPLE_GRID = 1


def mission_sha256(mission: ResearchMission) -> str:
    return sha256_text(canonical_json(mission.model_dump(mode="json")))


def plan_sha256(plan: InvestigationPlan) -> str:
    return sha256_text(canonical_json(plan.model_dump(mode="json")))


@dataclass
class PlanOutcome:
    plan: InvestigationPlan
    spec: DiscoverySpec | None = None  # effective discovery spec; None unless plan.status == "ready"
    model: Model | None = None
    resolutions: dict[str, InputResolution] = field(default_factory=dict)


def falsification_config(mission: ResearchMission) -> FalsificationConfig:
    f = mission.falsification
    cfg = FalsificationConfig(
        edge_margin=f.edge_margin,
        range_expansion=f.range_expansion,
        repeats=f.repeats,
        perturbation_levels=tuple(f.perturbation_levels),
        agree_tol=f.agree_tol,
        noise_tol=f.noise_tol,
    )
    cfg.validate()
    return cfg


def robustness_config(mission: ResearchMission) -> RobustnessConfig:
    r = mission.robustness
    cfg = RobustnessConfig(agree_tol=r.agree_tol, repeats=r.repeats)
    cfg.validate()
    return cfg


def evidence_sensitivity_config(mission: ResearchMission) -> EvidenceSensitivityConfig:
    return EvidenceSensitivityConfig(
        edge_margin=mission.robustness.edge_margin,
        repeats=mission.robustness.repeats,
        max_scenarios=mission.budget.max_scenarios,
    )


def build_perturbations(
    mission: ResearchMission, base_inputs: dict[str, tuple[float, str]]
) -> tuple[list[PerturbationSpec], list[str]]:
    """Configured relative perturbations at the CANONICAL base values of the discovery run. Inputs not used under
    the model options cannot be perturbed meaningfully and are skipped with a recorded note."""
    specs: list[PerturbationSpec] = []
    notes: list[str] = []
    for p in sorted(mission.robustness.perturbations, key=lambda q: q.variable_id):
        if p.variable_id not in base_inputs:
            notes.append(f"perturbation of {p.variable_id!r} skipped: not a required input under the model options")
            continue
        value, unit = base_inputs[p.variable_id]
        specs.append(relative_spec(p.variable_id, unit, value, p.half_width, p.n_evals, basis="configured"))
    return specs, notes


def estimate_evaluations(
    mission: ResearchMission,
    n_candidates: int,
    n_scenarios: int,
    search_params: list[ParameterRange] | None = None,
) -> tuple[int, str]:
    params = list(mission.search) if search_params is None else search_params
    search = sum(p.grid_points + SEARCH_BRENT_EVALS for p in params)
    if mission.backfire is not None:  # the hunt is capped by its own explicit budget
        search = min(search, mission.backfire.max_evaluations)
    regions = sum(
        REGION_FACTOR * r.x.grid_points * r.y.grid_points + 2 * SEARCH_BRENT_EVALS for r in mission.discovery.regions
    )
    sens = 0
    if mission.discovery.sensitivity is not None:
        s = mission.discovery.sensitivity
        sens = (2**s.n_samples_log2) * (s.target.grid_points + SEARCH_BRENT_EVALS) * SENSITIVITY_EVALS_PER_SAMPLE_GRID
    gmax = max(p.grid_points for p in params)
    n_pert = sum(p.n_evals for p in mission.robustness.perturbations)
    per_cand = gmax * (
        EVALS_PER_GRID_POINT_PER_CANDIDATE + EVALS_PER_PERTURBATION_PER_GRID_POINT * n_pert * mission.robustness.repeats
    )
    # evidence scenarios: one re-solve each (planned count: up to 3 per applicable record = lower/central/upper)
    per_cand += n_scenarios * (gmax + SEARCH_BRENT_EVALS)
    total = search + regions + sens + n_candidates * per_cand
    basis = (
        f"search {search} + regions {regions} + sensitivity {sens} + {n_candidates} candidate(s) x {per_cand} "
        f"(falsification/robustness + {n_scenarios} planned scenario(s)); an upper-bound estimate, not a measurement"
    )
    return int(total), basis


def _budget(
    mission: ResearchMission, n_scenarios: int, params: list[ParameterRange] | None = None
) -> EvaluationBudget:
    b = mission.budget
    violations: list[str] = []
    params = list(mission.search) if params is None else params
    if len(params) > b.max_search_parameters:
        violations.append(f"{len(params)} search parameters exceed max_search_parameters={b.max_search_parameters}")
    if mission.backfire is not None and mission.backfire.max_evaluations > b.max_model_evaluations:
        violations.append(
            f"backfire max_evaluations={mission.backfire.max_evaluations} exceeds "
            f"max_model_evaluations={b.max_model_evaluations}"
        )
    for p in params:
        if p.grid_points > b.max_grid_points_per_parameter:
            violations.append(
                f"{p.variable_id}: grid_points={p.grid_points} exceeds max_grid_points_per_parameter="
                f"{b.max_grid_points_per_parameter}"
            )
    if mission.discovery.top_k > b.max_candidates and mission.backfire is None:
        violations.append(f"top_k={mission.discovery.top_k} exceeds max_candidates={b.max_candidates}")
    if n_scenarios > b.max_scenarios:
        violations.append(f"{n_scenarios} planned evidence scenarios exceed max_scenarios={b.max_scenarios}")
    cap = b.max_candidates if mission.backfire is not None else min(mission.discovery.top_k, b.max_candidates)
    est, basis = estimate_evaluations(mission, cap, n_scenarios, params)
    if est > b.max_model_evaluations:
        violations.append(f"estimated {est} model evaluations exceed max_model_evaluations={b.max_model_evaluations}")
    return EvaluationBudget(
        limits=b,
        n_search_parameters=len(params),
        n_candidates_cap=cap,
        estimated_model_evaluations=est,
        estimate_basis=basis,
        within_budget=not violations,
        violations=violations,
    )


def _evidence_item(vid: str, required: bool, res: InputResolution, fallback: str | None) -> EvidenceItem:
    if res.state == "resolved" and res.selected is not None:
        s = res.selected
        return EvidenceItem(
            variable_id=vid,
            state="resolved",
            required=required,
            evidence_id=s.evidence_id,
            status=s.status,
            detail=f"{s.evidence_id} [{s.status}], rule: {res.selection_rule}",
        )
    alts = {r.evidence_id: float(r.conversion.canonical_value) for r in res.applicable}
    return EvidenceItem(
        variable_id=vid,
        state="conflicting" if res.state == "conflicting" else "missing",
        required=required,
        detail=describe_unresolved(res),
        alternatives=alts,
        fallback=fallback,
    )


def _invalid(mission: ResearchMission, problems: list[str], model_ref: str | None = None) -> PlanOutcome:
    plan = InvestigationPlan(
        mission_id=mission.mission_id,
        mission_sha256=mission_sha256(mission),
        status="invalid",
        reason="invalid_mission",
        problems=problems,
        model_ref=model_ref or mission.model_ref,
        options=dict(mission.options),
        stages=list(STAGES),
    )
    return PlanOutcome(plan)


def backfire_parameters(
    mission: ResearchMission,
    model: Model,
    required: list[str],
    resolutions: dict[str, InputResolution],
    explicit: list[ParameterRange],
) -> tuple[list[ParameterRange], BackfirePlan | None]:
    """Which parameters take part in the automated backfire hunt (deterministic, no search).

    Mission-declared search parameters always take part. With ``auto_parameters`` every other REQUIRED input that has
    a documented interval (the binding's own interval) is added; anything else is excluded with its reason. Bounds are
    never invented, never widened; units are converted by ``resolve_range`` at discovery time.
    """
    bf = mission.backfire
    if bf is None:
        return list(explicit), None
    explicit_ids = {p.variable_id for p in explicit}
    parts: list[ParameterRange] = list(explicit)
    planned: list[BackfirePlannedParameter] = []
    excluded: list[BackfireExclusion] = []

    def constraint(vid: str) -> str:
        res = resolutions.get(vid)
        if res is not None and res.state == "resolved" and res.selected is not None:
            return (
                f"registry evidence {res.selected.evidence_id} [{res.selected.status}] supplies the base value; the "
                "search range is the declared one and is not clipped to the evidence"
            )
        b = mission.bindings.get(vid)
        if b is None:
            return "no binding"
        return f"no registry evidence; base value is {b.provenance_class.value}; range is not evidence-constrained"

    for p in explicit:
        planned.append(
            BackfirePlannedParameter(
                variable_id=p.variable_id,
                source="mission_search",
                low=p.low,
                high=p.high,
                grid_points=p.grid_points,
                evidence_constraint=constraint(p.variable_id),
            )
        )
    for vid in sorted(v.id for v in model.spec.inputs):
        if vid in explicit_ids:
            continue
        if vid not in required:
            excluded.append(BackfireExclusion(variable_id=vid, reason="not a required input under the model options"))
            continue
        if not bf.auto_parameters:
            excluded.append(
                BackfireExclusion(variable_id=vid, reason="auto_parameters is off and it is not in [[search]]")
            )
            continue
        b = mission.bindings.get(vid)
        if (
            b is None
            or not isinstance(b.uncertainty, IntervalUncertainty)
            or not b.uncertainty.low < b.uncertainty.high
        ):
            excluded.append(
                BackfireExclusion(
                    variable_id=vid, reason="no documented interval to search; bounds are never invented"
                )
            )
            continue
        canon = model.spec.input_by_id(vid).canonical_unit
        lo = to_canonical(b.uncertainty.low, b.unit, canon, what=vid)
        hi = to_canonical(b.uncertainty.high, b.unit, canon, what=vid)
        parts.append(
            ParameterRange(
                variable_id=vid,
                basis=f"binding interval (auto-selected for the backfire hunt; {b.provenance_class.value})",
                grid_points=bf.default_grid_points,
            )
        )
        planned.append(
            BackfirePlannedParameter(
                variable_id=vid,
                source="auto_binding_interval",
                low=lo,
                high=hi,
                grid_points=bf.default_grid_points,
                evidence_constraint=constraint(vid),
            )
        )
    return parts, BackfirePlan(
        max_evaluations=bf.max_evaluations,
        auto_parameters=bf.auto_parameters,
        participating=planned,
        excluded=excluded,
    )


def plan_mission(mission: ResearchMission, registry: DatasetRegistry | None) -> PlanOutcome:
    """Build the investigation plan. Never raises for scientific outcomes (missing/conflicting evidence, budget):
    those become ``plan.status``/``plan.reason``. Malformed missions become ``status == "invalid"``."""
    problems: list[str] = []
    try:
        model = get_model(mission.model_ref)
        opts = model.resolve_options(mission.options)
        get_metric(model, mission.target_metric_ref)
    except EcoBackfireError as exc:
        return _invalid(mission, [f"{type(exc).__name__}: {exc}"])
    known = {v.id for v in model.spec.inputs}
    required = sorted(model.required_inputs(opts))

    def unknown(label: str, ids: list[str]) -> None:
        bad = sorted(set(ids) - known)
        if bad:
            problems.append(f"{label} reference unknown model inputs {bad}; model inputs are {sorted(known)}")

    unknown("bindings", list(mission.bindings))
    unknown("evidence_requirements", list(mission.evidence_requirements))
    unknown("search parameters", [p.variable_id for p in mission.search])
    unknown("robustness perturbations", [p.variable_id for p in mission.robustness.perturbations])
    if mission.discovery.sensitivity is not None:
        s = mission.discovery.sensitivity
        unknown("sensitivity", [s.target.variable_id, *(p.variable_id for p in s.parameters)])
    for r in mission.discovery.regions:
        unknown("regions", [r.x.variable_id, r.y.variable_id])
    for p in mission.search:
        if p.low is None and p.variable_id not in mission.bindings:
            problems.append(
                f"{p.variable_id}: no explicit low/high and no binding interval to use; bounds are never invented"
            )
        elif p.low is None and mission.bindings[p.variable_id].uncertainty.kind != "interval":
            problems.append(f"{p.variable_id}: no explicit low/high and the binding has no interval; give a range")
    for label, build in (
        ("falsification settings", falsification_config),
        ("robustness settings", robustness_config),
        ("robustness settings", evidence_sensitivity_config),
    ):
        try:
            build(mission)
        except (EcoBackfireError, ValueError) as exc:
            problems.append(f"{label}: {exc}")
    if problems:
        return _invalid(mission, problems, model.spec.ref)

    # ---- evidence resolution (the registry is queried once per requirement, never raising for missing/conflict)
    items: list[EvidenceItem] = []
    resolutions: dict[str, InputResolution] = {}
    resolved_requests: dict[str, EvidenceRequest] = {}
    reg_problem: str | None = None
    for vid in sorted(mission.evidence_requirements):
        req = mission.evidence_requirements[vid]
        fallback = "uses the mission's own binding and reports it as an assumption" if not req.required else None
        if registry is None:
            reg_problem = "evidence is required but no dataset registry was supplied"
            items.append(
                EvidenceItem(
                    variable_id=vid, state="missing", required=req.required, detail=reg_problem, fallback=fallback
                )
            )
            continue
        request = req.to_request(mission.scope)
        try:
            res = resolve_parameter(registry, model.spec, vid, request)
        except MissingEvidenceError as exc:  # unknown dataset / version: evidence is missing, not invented
            items.append(
                EvidenceItem(
                    variable_id=vid,
                    state="missing",
                    required=req.required,
                    detail=f"{type(exc).__name__}: {exc}",
                    fallback=fallback,
                )
            )
            continue
        except ConfigError as exc:
            problems.append(f"{vid}: {exc}")
            continue
        resolutions[vid] = res
        items.append(_evidence_item(vid, req.required, res, fallback))
        if res.state == "resolved":
            resolved_requests[vid] = request
    if problems:
        return _invalid(mission, problems, model.spec.ref)

    resolved = sorted(i.variable_id for i in items if i.state == "resolved")
    conflicting = sorted(i.variable_id for i in items if i.state == "conflicting")
    missing = sorted(i.variable_id for i in items if i.state == "missing")
    blocking_missing = [i.variable_id for i in items if i.state == "missing" and i.required]
    optional_missing = [i.variable_id for i in items if i.state == "missing" and not i.required]

    assumptions: list[str] = []
    inputs: list[PlannedInput] = []
    uncovered: list[str] = []
    for vid in sorted(known):
        b: Binding | None = mission.bindings.get(vid)
        searched = any(p.variable_id == vid for p in mission.search)
        if vid not in required:
            inputs.append(
                PlannedInput(variable_id=vid, role="unused_under_options", detail=f"not required under options {opts}")
            )
            continue
        if vid in resolved:
            ev = next(i for i in items if i.variable_id == vid)
            inputs.append(
                PlannedInput(
                    variable_id=vid,
                    role="evidence",
                    provenance_class=resolutions[vid].selected.provenance_class.value
                    if resolutions[vid].selected
                    else None,
                    detail=f"from {ev.evidence_id}" + (" (searched)" if searched else ""),
                )
            )
        elif b is not None and not b.missing and vid not in conflicting:
            role = "illustrative" if b.provenance_class is ProvenanceClass.ILLUSTRATIVE else "assumed"
            tag = " (fallback: evidence requested but missing)" if vid in optional_missing else ""
            inputs.append(
                PlannedInput(
                    variable_id=vid,
                    role=role,
                    provenance_class=b.provenance_class.value,
                    detail=f"{b.value} {b.unit} from the mission's own binding{tag}",
                )
            )
            assumptions.append(f"{vid} = {b.value} {b.unit} is {b.provenance_class.value}{tag}")
        elif vid in conflicting:
            inputs.append(PlannedInput(variable_id=vid, role="missing", detail="applicable evidence records conflict"))
        else:
            inputs.append(PlannedInput(variable_id=vid, role="missing", detail="no binding and no resolved evidence"))
            uncovered.append(vid)

    hunt_params, backfire_plan = backfire_parameters(mission, model, required, resolutions, list(mission.search))
    explicit_ids = {p.variable_id for p in mission.search}
    dims = [
        SearchDimension(
            variable_id=p.variable_id,
            low=p.low,
            high=p.high,
            grid_points=p.grid_points,
            basis=p.basis,
            range_source=(
                "auto_binding_interval"
                if p.variable_id not in explicit_ids
                else ("explicit" if p.low is not None else "binding_interval")
            ),
        )
        for p in hunt_params
    ]
    # upper bound: every resolved input is a scenario source for every candidate (a candidate skips only its own)
    n_scen = sum(3 * max(1, len(resolutions[v].applicable)) for v in resolved if v in resolutions)
    budget = _budget(mission, n_scen, hunt_params)

    def finish(
        status: PlanStatus, reason: FailureCode | None, why: list[str], spec: DiscoverySpec | None
    ) -> PlanOutcome:
        plan = InvestigationPlan(
            mission_id=mission.mission_id,
            mission_sha256=mission_sha256(mission),
            status=status,
            reason=reason,
            problems=why,
            model_ref=model.spec.ref,
            options=dict(opts),
            system_boundary_id=model.system_boundary(opts).id,
            required_inputs=required,
            inputs=inputs,
            evidence=items,
            resolved_evidence=resolved,
            missing_evidence=sorted(set(missing) | set(uncovered)),
            conflicting_evidence=conflicting,
            assumptions=assumptions,
            search_dimensions=dims,
            budget=budget,
            stages=list(STAGES),
            discovery_spec_sha256=None if spec is None else spec_sha256(spec),
            backfire=backfire_plan,
        )
        return PlanOutcome(plan, spec, model, resolutions)

    if conflicting:
        return finish(
            "unresolved",
            "conflicting_evidence",
            [i.detail for i in items if i.state == "conflicting"],
            None,
        )
    if blocking_missing or uncovered or reg_problem:
        why = [i.detail for i in items if i.state == "missing" and i.required]
        why += [f"input {v!r} has neither a binding nor resolved evidence" for v in uncovered]
        return finish("unresolved", "missing_evidence", why, None)
    if not resolved:
        return finish(
            "unresolved",
            "missing_evidence",
            [
                "no evidence requirement could be resolved (all optional and missing); there is nothing documented "
                "to challenge the candidates with"
            ],
            None,
        )
    if not budget.within_budget:
        return finish("over_budget", "budget_exceeded", budget.violations, None)

    # Mission bounds are in the model's CANONICAL unit; discovery reads them in the binding's own unit (for an
    # evidence-resolved input: the record's unit, e.g. gCO2e/kWh). Convert explicitly so units never shift silently.
    search_effective = []
    for p in hunt_params:
        if p.low is None or p.high is None:
            search_effective.append(p)
            continue
        canon = model.spec.input_by_id(p.variable_id).canonical_unit
        if p.variable_id in resolutions and resolutions[p.variable_id].selected is not None:
            bunit = resolutions[p.variable_id].selected.unit  # type: ignore[union-attr]
        else:
            bunit = mission.bindings[p.variable_id].unit
        if bunit != canon:
            p = p.model_copy(
                update={
                    "low": to_canonical(p.low, canon, bunit, what=p.variable_id),
                    "high": to_canonical(p.high, canon, bunit, what=p.variable_id),
                }
            )
        search_effective.append(p)

    # ---- effective discovery spec: resolved requests supply their input; their fallback bindings are dropped
    spec = DiscoverySpec(
        discovery_id=mission.mission_id,
        title=mission.title,
        research_question=mission.research_question,
        model_ref=mission.model_ref,
        options=dict(mission.options),
        bindings={k: v for k, v in mission.bindings.items() if k not in resolved_requests},
        evidence_requests=resolved_requests,
        decision_metric_ref=mission.target_metric_ref,
        parameters=search_effective,
        regions=list(mission.discovery.regions),
        sensitivity=mission.discovery.sensitivity,
        weights=mission.discovery.weights,
        backfire=None if mission.backfire is None else mission.backfire.to_spec(),
    )
    return finish("ready", None, [], spec)
