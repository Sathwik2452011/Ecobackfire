"""Research-mission contracts (layer 0): the machine-readable input and output of one EcoBackfire investigation.

A ``ResearchMission`` declares WHAT is investigated (question, intervention, model, target metric, evidence
requirements, scopes, search bounds, falsification/robustness settings, computational budget). It contains no
code and needs no LLM. Given fixed inputs (mission file, dataset releases, model version) every downstream
step is deterministic.

A ``MissionResult`` is what another program or a future UI consumes. It reuses the existing contracts
(``Candidate``, ``CandidateEvidenceEvaluation``, ``EvidenceResolutionSummary``, ...) instead of inventing new ones,
and keeps four concepts apart on purpose:

* evidence documentation  - how well the inputs are documented (never validity),
* robustness              - what survived falsification / perturbation / evidence challenges (categorical),
* scientific result       - the model-derived hypothesis and its claim type (conditional on non-evidence inputs),
* candidate ranking       - an attention-ordering score; NOT a probability that anything is true.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, SerializerFunctionWrapHandler, field_validator, model_serializer, model_validator

from ecobackfire.contracts.common import ILLUSTRATIVE_MARKER, Frozen, ProvenanceClass
from ecobackfire.contracts.discovery import (
    BackfireBaseline,
    BackfireExclusion,
    BackfireSearchSpec,
    Candidate,
    ParameterRange,
    RegionSearch,
    RejectedCandidate,
    ScoringWeights,
    SensitivitySpec,
)
from ecobackfire.contracts.evidence_sensitivity import (
    ChallengeResult,
    ClassificationDecision,
    RobustnessDimension,
)
from ecobackfire.contracts.experiment import Binding
from ecobackfire.contracts.resolution import (
    EvidenceRequest,
    EvidenceResolutionSummary,
    RecordStatusName,
    TemporalMatch,
    omit_empty,
)

MISSION_SCHEMA_VERSION = "1.0.0"
MISSION_RESULT_SCHEMA_VERSION = "1.0.0"


# ---------------------------------------------------------------- mission (input)


class MissionScope(Frozen):
    """Geographic and temporal scope of the investigation. Evidence requirements inherit it unless they override."""

    geography: str
    period: str

    @model_validator(mode="after")
    def _nonempty(self) -> MissionScope:
        if not self.geography.strip() or not self.period.strip():
            raise ValueError("scope geography and period must be non-empty")
        return self


class MissionEvidenceRequirement(Frozen):
    """One model input that must (or may) come from the dataset registry. Keyed by variable id in the mission.

    ``required = true``: if the registry cannot supply it the mission is ``unresolved`` (``missing_evidence``);
    no value is invented. ``required = false``: a missing record falls back to the mission's own binding for that
    input, which is then reported as an assumption (a binding must exist). Conflicting records are never
    resolved silently, whatever ``required`` says; pin one with ``select``.
    """

    dataset_id: str
    version: str
    geography: str | None = None  # default: the mission scope
    period: str | None = None  # default: the mission scope
    required: bool = True
    geography_fallback: bool = False
    temporal_match: TemporalMatch = "contains"
    minimum_status: RecordStatusName | None = None
    select: str | None = None
    note: str | None = None

    def to_request(self, scope: MissionScope) -> EvidenceRequest:
        return EvidenceRequest(
            dataset_id=self.dataset_id,
            version=self.version,
            geography=self.geography or scope.geography,
            period=self.period or scope.period,
            geography_fallback=self.geography_fallback,
            temporal_match=self.temporal_match,
            minimum_status=self.minimum_status,
            select=self.select,
            note=self.note,
        )


class DiscoveryObjective(Frozen):
    """What discovery looks for. Only the existing deterministic break-even scan is supported."""

    kind: Literal["breakeven_scan"] = "breakeven_scan"
    #: how many of the best-ranked candidates are falsified/robustness-tested and reported in full
    top_k: int = 3
    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    regions: list[RegionSearch] = Field(default_factory=list)
    sensitivity: SensitivitySpec | None = None

    @field_validator("top_k")
    @classmethod
    def _k(cls, v: int) -> int:
        if v < 1:
            raise ValueError("top_k must be >= 1")
        return v


class BackfireSettings(BackfireSearchSpec):
    """Automated backfire hunt for the mission (see ``BackfireSearchSpec``; ``max_evaluations`` is mandatory).

    With ``auto_parameters`` the hunt also searches every required input that has a DOCUMENTED interval (the binding's
    own interval uncertainty) and is not already listed in ``[[search]]``: the mission need not enumerate them. Bounds
    are never invented: an input without a documented interval is excluded and the reason is recorded.
    """

    auto_parameters: bool = True
    default_grid_points: int = 201

    @model_validator(mode="after")
    def _auto(self) -> BackfireSettings:
        if self.default_grid_points < 3:
            raise ValueError("default_grid_points must be >= 3")
        return self

    def to_spec(self) -> BackfireSearchSpec:
        return BackfireSearchSpec.model_validate(
            self.model_dump(exclude={"auto_parameters", "default_grid_points"})
        )


class FalsificationSettings(Frozen):
    """Thresholds of the numerical falsification checks X1-X7 (documented policy choices; defaults equal the
    existing ``FalsificationConfig`` defaults)."""

    edge_margin: float = 0.02
    range_expansion: float = 0.25
    repeats: int = 3
    perturbation_levels: list[float] = Field(default_factory=lambda: [0.01, 0.05, 0.10])
    agree_tol: float = 1e-8
    noise_tol: float = 1e-6


class MissionPerturbation(Frozen):
    """A configured relative perturbation range for one input (``baseline * (1 -/+ half_width)``).

    This is a configured sensitivity experiment, NOT an uncertainty distribution; the result says so."""

    variable_id: str
    half_width: float
    n_evals: int = 5

    @model_validator(mode="after")
    def _ok(self) -> MissionPerturbation:
        if not self.variable_id.strip():
            raise ValueError("variable_id must be non-empty")
        if not (math.isfinite(self.half_width) and 0 < self.half_width < 1):
            raise ValueError("half_width must be in (0, 1)")
        if not 2 <= self.n_evals <= 101:
            raise ValueError("n_evals must be between 2 and 101")
        return self


class RobustnessSettings(Frozen):
    perturbations: list[MissionPerturbation] = Field(default_factory=list)
    edge_margin: float = 0.02
    repeats: int = 2  # whole evaluation repeated and compared bit-for-bit
    agree_tol: float = 1e-8


class MissionBudget(Frozen):
    """Explicit computational limits. Exceeding any limit is a refusal (``budget_exceeded``), never truncation."""

    max_search_parameters: int = 12
    max_grid_points_per_parameter: int = 2001
    max_candidates: int = 10  # hard cap on candidates the discovery stage may hand to falsification
    max_scenarios: int = 500  # evidence scenarios per candidate
    max_model_evaluations: int = 5_000_000  # compared with a documented UPPER-BOUND estimate (see planner)

    @model_validator(mode="after")
    def _pos(self) -> MissionBudget:
        for k, v in self.model_dump().items():
            if v < 1:
                raise ValueError(f"budget {k} must be >= 1")
        return self


class ResearchMission(Frozen):
    schema_version: str = MISSION_SCHEMA_VERSION
    mission_id: str
    title: str
    research_question: str
    #: the intervention / scenario under study, in words (the model is what actually computes it)
    intervention: str
    scenario: str = ""
    model_ref: str
    options: dict[str, str] = Field(default_factory=dict)
    target_metric_ref: str  # decision metric of the model, e.g. delta_emissions_beneficial
    target_environmental_metric: str  # human-readable: which environmental quantity the metric stands for
    scope: MissionScope
    #: every model input needed under ``options`` must be bound here, supplied by an evidence requirement, or
    #: the mission is unresolved. Bindings of evidence-requested inputs are fallbacks (see MissionEvidenceRequirement).
    bindings: dict[str, Binding]
    evidence_requirements: dict[str, MissionEvidenceRequirement]
    search: list[ParameterRange]
    discovery: DiscoveryObjective = Field(default_factory=DiscoveryObjective)
    falsification: FalsificationSettings = Field(default_factory=FalsificationSettings)
    robustness: RobustnessSettings = Field(default_factory=RobustnessSettings)
    budget: MissionBudget = Field(default_factory=MissionBudget)
    #: must be true when any binding is illustrative: the result is then explicitly a model-derived, conditional
    #: result about the model, not about a real system.
    acknowledges_illustrative_inputs: bool = False
    #: re-run the whole scientific pipeline once and compare (reproducibility self-check)
    reproducibility_check: bool = True
    #: automated backfire hunt (omitted from the serialized mission when absent, so existing mission hashes hold)
    backfire: BackfireSettings | None = None

    @model_serializer(mode="wrap")
    def _omit_unused_backfire(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        return omit_empty(handler(self), "backfire")

    @field_validator("model_ref")
    @classmethod
    def _exact_version(cls, v: str) -> str:
        if "@" not in v or v.endswith("@latest") or v.endswith("@"):
            raise ValueError("model_ref must be 'model_id@exact.version' (no 'latest')")
        return v

    @field_validator("mission_id")
    @classmethod
    def _id_ok(cls, v: str) -> str:
        if not v or any(c.isspace() or c in "/\\" for c in v):
            raise ValueError("mission_id must be non-empty with no whitespace or path separators")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> ResearchMission:
        for name in ("title", "research_question", "intervention", "target_metric_ref", "target_environmental_metric"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must be non-empty")
        if not self.search:
            raise ValueError("a mission needs at least one search parameter (search bounds are never invented)")
        ids = [p.variable_id for p in self.search]
        if len(ids) != len(set(ids)):
            raise ValueError("search parameters must be unique by variable_id")
        if not self.evidence_requirements:
            raise ValueError(
                "a mission needs at least one evidence requirement: evidence-sensitive evaluation challenges the "
                "candidates with documented evidence, and without any request there is nothing to challenge them with"
            )
        for vid, req in self.evidence_requirements.items():
            if not req.required and vid not in self.bindings:
                raise ValueError(
                    f"{vid}: an optional evidence requirement needs a fallback binding (otherwise it is required)"
                )
        pids = [p.variable_id for p in self.robustness.perturbations]
        if len(pids) != len(set(pids)):
            raise ValueError("robustness perturbations must be unique by variable_id")
        illustrative = sorted(k for k, b in self.bindings.items() if b.provenance_class is ProvenanceClass.ILLUSTRATIVE)
        if illustrative and not self.acknowledges_illustrative_inputs:
            raise ValueError(
                f"inputs {illustrative} are illustrative; set acknowledges_illustrative_inputs = true to state that "
                "the mission's result is a model-derived, conditional result and not a claim about a real system"
            )
        if ILLUSTRATIVE_MARKER in self.research_question:  # pragma: no cover - guard against marker misuse
            raise ValueError("the illustrative marker belongs on illustrative bindings, not in the question")
        return self


# ---------------------------------------------------------------- plan


PlanStatus = Literal["ready", "unresolved", "invalid", "over_budget"]
FailureCode = Literal[
    "invalid_mission",
    "missing_evidence",
    "conflicting_evidence",
    "budget_exceeded",
    "discovery_failed",
    "no_candidates",
    "falsification_failed",
    "robustness_failed",
    "classification_failed",
    "reproducibility_failed",
    "artifact_failed",
]


class PlannedInput(Frozen):
    variable_id: str
    role: Literal["searched", "evidence", "assumed", "illustrative", "unused_under_options", "missing"]
    provenance_class: str | None = None
    detail: str


class SearchDimension(Frozen):
    variable_id: str
    low: float | None
    high: float | None
    grid_points: int
    basis: str
    range_source: Literal["explicit", "binding_interval", "auto_binding_interval"]


class BackfirePlannedParameter(Frozen):
    variable_id: str
    source: Literal["mission_search", "auto_binding_interval"]
    low: float | None
    high: float | None
    grid_points: int
    #: what the registry documents about this input and how the search range relates to it (disclosure, no clipping)
    evidence_constraint: str


class BackfirePlan(Frozen):
    """Which parameters take part in the automated backfire hunt, which were excluded and why."""

    max_evaluations: int
    auto_parameters: bool
    participating: list[BackfirePlannedParameter] = Field(default_factory=list)
    excluded: list[BackfireExclusion] = Field(default_factory=list)


class EvaluationBudget(Frozen):
    limits: MissionBudget
    n_search_parameters: int
    n_candidates_cap: int
    estimated_model_evaluations: int  # documented UPPER-BOUND estimate, not a measurement
    estimate_basis: str
    within_budget: bool
    violations: list[str]


class EvidenceItem(Frozen):
    variable_id: str
    state: Literal["resolved", "conflicting", "missing"]
    required: bool
    evidence_id: str | None = None
    status: str | None = None
    detail: str
    #: for conflicts: every applicable alternative is preserved here (evidence id -> canonical value)
    alternatives: dict[str, float] = Field(default_factory=dict)
    fallback: str | None = None  # how an unresolved optional requirement is handled


class InvestigationPlan(Frozen):
    schema_version: str = MISSION_RESULT_SCHEMA_VERSION
    mission_id: str
    mission_sha256: str
    status: PlanStatus
    reason: FailureCode | None = None
    problems: list[str] = Field(default_factory=list)
    model_ref: str
    options: dict[str, str] = Field(default_factory=dict)
    system_boundary_id: str | None = None
    required_inputs: list[str] = Field(default_factory=list)
    inputs: list[PlannedInput] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    resolved_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    conflicting_evidence: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    search_dimensions: list[SearchDimension] = Field(default_factory=list)
    budget: EvaluationBudget | None = None
    stages: list[str] = Field(default_factory=list)
    #: sha256 of the effective DiscoverySpec the plan will run (None when the plan is not ready)
    discovery_spec_sha256: str | None = None
    backfire: BackfirePlan | None = None

    @model_serializer(mode="wrap")
    def _omit_unused_backfire(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        return omit_empty(handler(self), "backfire")


# ---------------------------------------------------------------- result


MissionStatus = Literal["completed", "completed_no_candidates", "unresolved", "invalid", "failed"]


class StageRecord(Frozen):
    stage: str
    status: Literal["ok", "failed", "skipped", "not_reached"]
    detail: str = ""


class MissionFailure(Frozen):
    stage: str
    code: FailureCode
    message: str
    details: list[str] = Field(default_factory=list)


class AssumptionRecord(Frozen):
    variable_id: str
    provenance_class: str
    value: float | None = None
    unit: str | None = None
    note: str = ""
    kind: Literal["illustrative", "assumed", "fallback_for_missing_evidence"]


class ModelSummary(Frozen):
    model_ref: str
    options: dict[str, str]
    system_boundary_id: str
    functional_unit: str
    decision_metric_ref: str
    target_environmental_metric: str


class RankedCandidate(Frozen):
    """One candidate with its four separate concepts. The ranking score is an attention ordering."""

    rank: int  # evidence-aware rank: robustness tier first, then attention score
    candidate: Candidate  # the scientific result (a model-derived hypothesis; label stays 'candidate')
    ranking_score: float  # the existing transparent attention score; NOT a probability of truth
    ranking_note: str
    score_axes: dict[
        str, object
    ]  # the existing three never-merged axes: evidence documentation / validity / robustness
    robustness_classification: str | None  # categorical; None when robustness could not be assessed
    claim_type: Literal["model_derived_conditional", "model_derived_illustrative", "not_assessed"]
    evaluated: bool  # False: reported from discovery only (outside top_k or the stage failed)


class FalsificationView(Frozen):
    candidate_id: str
    status: Literal["assessed", "not_assessed"]
    checks: list[ChallengeResult] = Field(default_factory=list)  # numerical falsification checks, failures included
    settings: dict[str, object] = Field(default_factory=dict)  # the falsification settings the engine recorded


class RobustnessView(Frozen):
    candidate_id: str
    status: Literal["assessed", "not_assessed"]
    checks: list[ChallengeResult] = Field(default_factory=list)  # perturbation / evidence-range / source challenges
    dimensions: list[RobustnessDimension] = Field(default_factory=list)  # three dimensions, never merged
    deterministic: bool | None = None
    settings: dict[str, object] = Field(default_factory=dict)  # robustness config + perturbations the engine recorded


class ClassificationView(Frozen):
    candidate_id: str
    decision: ClassificationDecision | None  # None = not classified (never defaulted to robust)


class EvidenceAvailability(Frozen):
    """What the registry held for one evidence-requested input (documentation of availability, not validity)."""

    variable_id: str
    state: str
    evidence_id: str | None = None
    status: str | None = None
    n_sources: int | None = None
    n_documented_ranges: int | None = None
    n_alternative_records: int = 0
    n_scenarios: int | None = None
    has_documented_variation: bool = False


class EvidenceSummary(Frozen):
    """Counts of the evidence state of the finished investigation. Computed from the evaluations; never hardcoded.

    Counting only: it does not alter any classification. See ``definitions`` for each counter."""

    total_candidates: int
    candidates_evaluated: int
    candidates_not_evaluated: int
    candidates_with_evidence_variation: int
    evidence_insufficient: int
    source_dependent: int
    eliminated_by_evidence: int
    evidence_robust: int
    evidence_inconclusive: int
    by_evidence_class: dict[str, int] = Field(default_factory=dict)
    evidence_available: list[EvidenceAvailability] = Field(default_factory=list)
    inputs_without_registry_evidence: list[str] = Field(default_factory=list)
    statement: str
    definitions: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class SettingsFinding(Frozen):
    code: str
    severity: Literal["warning", "info"]
    message: str
    observed: object = None
    reference: object = None


class SettingsAudit(Frozen):
    status: Literal["warnings", "no_weakening_vs_project_defaults"]
    n_warnings: int
    findings: list[SettingsFinding] = Field(default_factory=list)
    reference: str
    note: str


class AnalysisSettings(Frozen):
    """The falsification / robustness / evidence-challenge settings actually used, in one place."""

    applied: bool  # False: declared only (the analysis stages were not reached)
    perturbation_scheme: Literal["one_at_a_time"] = "one_at_a_time"
    falsification: dict[str, object] = Field(default_factory=dict)
    robustness: dict[str, object] = Field(default_factory=dict)
    robustness_perturbations_applied: list[dict[str, object]] = Field(default_factory=list)
    robustness_perturbed_parameters: list[str] = Field(default_factory=list)
    robustness_perturbations_skipped: list[str] = Field(default_factory=list)
    evidence_challenge: dict[str, object] = Field(default_factory=dict)
    scope_statement: str
    audit: SettingsAudit


class VerificationDisclosure(Frozen):
    suite: str
    performed: bool
    verified_candidate_ids: list[str] = Field(default_factory=list)
    verification_label: str | None = None
    falsified_and_robustness_tested_not_verified: list[str] = Field(default_factory=list)
    discovery_only_candidate_ids: list[str] = Field(default_factory=list)
    statement: str


class BudgetUsage(Frozen):
    configured_max_model_evaluations: int
    estimated_upper_bound_at_plan_time: int | None = None
    actual_model_evaluations: int | None = None
    actual_model_evaluations_reproducibility_rerun: int | None = None
    budget_reached: bool | None = None
    remaining_model_evaluations: int | None = None
    fraction_used: float | None = None
    max_scenarios_per_candidate: int
    max_scenarios_used_by_any_candidate: int = 0
    candidates_evaluated: int = 0
    max_candidates: int
    statement: str
    counting_scope: str
    estimate_note: str


class MissionReproducibility(Frozen):
    mission_sha256: str
    plan_sha256: str | None = None
    discovery_spec_sha256: str | None = None
    evidence_resolution_sha256: str | None = None
    evidence_registry_sha256: str | None = None
    datasets: list[str] = Field(default_factory=list)  # "<id>@<version> content_sha256=..."
    model_ref: str
    seed: int | None = None  # no randomness is used unless a sensitivity analysis declares its own seed
    sensitivity_seed: int | None = None
    dependencies: dict[str, str] = Field(default_factory=dict)
    scientific_results_sha256: str | None = None  # hash of the candidates/falsification/robustness/classification
    check_performed: bool = False
    check_outcome: Literal["reproduced_exactly", "reproduced_within_tolerance", "not_reproduced", "not_performed"] = (
        "not_performed"
    )
    check_details: list[str] = Field(default_factory=list)
    reproduce_command: str = ""


CandidateFate = Literal["survived", "survived_limited", "sensitive", "eliminated", "not_assessed"]


class BackfireOutcome(Frozen):
    """What happened to ONE automatically discovered candidate. Eliminated candidates are kept, with the reasons."""

    candidate_id: str
    variable_id: str
    value: float
    unit: str
    origin: str  # global_grid | local_refinement
    rank: int
    fate: CandidateFate
    classification: str | None
    evidence_class: str | None
    falsification_failed_checks: list[str]
    robustness_failed_checks: list[str]
    reasons: list[str]


class BackfireSearchedRegion(Frozen):
    variable_id: str
    unit: str
    low: float | None
    high: float | None
    grid_points: int
    source: str
    evidence_constraint: str
    behaviour: str
    n_reversal_intervals: int


class BackfireMissionSummary(Frozen):
    """Answers, for one mission: baseline, what/where was searched, how many evaluations and crossings, which candidates
    survived falsification / robustness, which are evidence-sensitive, and the strongest finding. Counting and copying
    of results produced elsewhere; nothing here classifies or scores."""

    status: Literal["complete", "budget_exhausted", "no_searchable_parameters"]
    objective: str
    baseline: BackfireBaseline
    parameters_searched: list[str]
    parameters_excluded: list[BackfireExclusion]
    regions: list[BackfireSearchedRegion]
    evaluations_performed: int
    max_evaluations: int
    candidates_found: int
    candidates_from_refinement: int
    searches_rejected: int
    candidates_assessed: int
    candidates_not_assessed: int
    candidates_falsified: list[str]
    survived_falsification: list[str]
    survived_robustness: list[str]
    evidence_robust: list[str]
    evidence_sensitive: list[str]
    evidence_insufficient: list[str]
    eliminated: list[str]
    outcomes: list[BackfireOutcome]
    strongest_finding: str | None  # candidate id
    strongest_finding_statement: str
    limitations: list[str]


class MissionResult(Frozen):
    schema_version: str = MISSION_RESULT_SCHEMA_VERSION
    mission: ResearchMission
    status: MissionStatus
    reason: FailureCode | None = None
    failure: MissionFailure | None = None
    stages: list[StageRecord] = Field(default_factory=list)
    model: ModelSummary | None = None
    plan: InvestigationPlan | None = None
    #: registry resolution detail (was named ``evidence_summary`` before the counting summary below existed)
    evidence_resolution: EvidenceResolutionSummary | None = None
    #: counts of the evidence state of the finished investigation (candidates with evidence variation, ...)
    evidence_summary: EvidenceSummary | None = None
    evidence_items: list[EvidenceItem] = Field(
        default_factory=list
    )  # resolved / missing / conflicting, alternatives kept
    assumptions: list[AssumptionRecord] = Field(default_factory=list)
    candidates: list[RankedCandidate] = Field(default_factory=list)
    rejected_searches: list[RejectedCandidate] = Field(default_factory=list)
    falsification: list[FalsificationView] = Field(default_factory=list)
    robustness: list[RobustnessView] = Field(default_factory=list)
    classification: list[ClassificationView] = Field(default_factory=list)
    analysis_settings: AnalysisSettings | None = None
    verification_disclosure: VerificationDisclosure | None = None
    budget_usage: BudgetUsage | None = None
    backfire: BackfireMissionSummary | None = None
    scientific_result: str = ""  # one honest sentence: what the model-derived result is, and what it is not
    provenance: dict[str, object] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)  # file name -> sha256 (excluding this file and manifests)
    reproducibility: MissionReproducibility
    warnings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _omit_unused_backfire(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        return omit_empty(handler(self), "backfire")
