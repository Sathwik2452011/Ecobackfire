"""Discovery contracts: DiscoverySpec (input) and Candidate / RejectedCandidate / DiscoveryResult (output).

Everything a candidate reports is a *derived numerical result of the declared model under declared
inputs*. A candidate is a hypothesis requiring further validation, never an empirical finding.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, SerializerFunctionWrapHandler, field_validator, model_serializer, model_validator

from ecobackfire.contracts.common import (
    ILLUSTRATIVE_MARKER,
    Frozen,
    FunctionalUnit,
    ProvenanceClass,
    QuantityValue,
    SystemBoundary,
)
from ecobackfire.contracts.experiment import Binding
from ecobackfire.contracts.resolution import (
    EvidenceRequest,
    EvidenceResolutionSummary,
    InputEvidenceRef,
    omit_empty,
)

DISCOVERY_SCHEMA_VERSION = "1.0.0"

#: Provenance classes allowed for an explicitly declared search range in this phase. measured/sourced
#: ranges need the evidence store to be wired into discovery (Slice D) and are refused until then.
RANGE_ALLOWED_CLASSES = (
    ProvenanceClass.USER_SUPPLIED,
    ProvenanceClass.ESTIMATED,
    ProvenanceClass.ILLUSTRATIVE,
)


class ScoringWeights(Frozen):
    """Weights of the criteria applied in this step. Must be non-negative and sum to 1."""

    proximity: float = 0.4
    magnitude: float = 0.3
    evidence: float = 0.3

    @model_validator(mode="after")
    def _check(self) -> ScoringWeights:
        vals = (self.proximity, self.magnitude, self.evidence)
        if any((not math.isfinite(v)) or v < 0 for v in vals):
            raise ValueError("scoring weights must be finite and non-negative")
        if not math.isclose(sum(vals), 1.0, abs_tol=1e-9):
            raise ValueError(f"scoring weights must sum to 1, got {sum(vals)}")
        return self


class ParameterRange(Frozen):
    """A bounded 1-D search over one model input, other inputs held at their bound values.

    ``low``/``high`` are in the binding's own unit. If both are omitted, the binding's interval
    uncertainty is used (and must exist). Ranges are never invented by the engine.
    """

    variable_id: str
    low: float | None = None
    high: float | None = None
    basis: str  # why this range; required so every range is a documented choice
    provenance_class: ProvenanceClass = ProvenanceClass.USER_SUPPLIED
    grid_points: int = 201
    xtol: float = 1e-12

    @model_validator(mode="after")
    def _check(self) -> ParameterRange:
        if not self.variable_id.strip() or not self.basis.strip():
            raise ValueError("variable_id and basis must be non-empty")
        if (self.low is None) != (self.high is None):
            raise ValueError("give both low and high, or neither (to use the binding's interval)")
        if self.low is not None and self.high is not None:
            if not (math.isfinite(self.low) and math.isfinite(self.high)) or not self.low < self.high:
                raise ValueError(f"range requires finite low < high, got [{self.low}, {self.high}]")
            if self.provenance_class not in RANGE_ALLOWED_CLASSES:
                raise ValueError(
                    f"explicit ranges may be user_supplied, estimated or illustrative in this phase, "
                    f"not '{self.provenance_class.value}' (evidence-backed ranges arrive with Slice D)"
                )
            if self.provenance_class is ProvenanceClass.ILLUSTRATIVE and ILLUSTRATIVE_MARKER not in self.basis:
                raise ValueError(f"illustrative ranges must carry {ILLUSTRATIVE_MARKER!r} in basis")
        if self.grid_points < 3:
            raise ValueError("grid_points must be >= 3")
        if not (math.isfinite(self.xtol) and self.xtol > 0):
            raise ValueError("xtol must be positive and finite")
        return self


class RegionSearch(Frozen):
    """A 2-D structured scan over two inputs (all others at base): where does the decision reverse?"""

    x: ParameterRange
    y: ParameterRange

    @model_validator(mode="after")
    def _distinct(self) -> RegionSearch:
        if self.x.variable_id == self.y.variable_id:
            raise ValueError("a region search needs two different variables")
        return self


class SensitivitySpec(Frozen):
    """Seeded quasi-Monte-Carlo screening of which inputs move one break-even location.

    ``target`` is the searched variable and its search range (the break-even is its first converged
    crossing). ``parameters`` are sampled over their ranges (``grid_points``/``xtol`` on them are unused).
    The seed has no default on purpose: every stochastic analysis must record one.
    """

    target: ParameterRange
    parameters: list[ParameterRange]
    seed: int
    n_samples_log2: int = 8

    @model_validator(mode="after")
    def _check(self) -> SensitivitySpec:
        if isinstance(self.seed, bool) or self.seed < 0:
            raise ValueError("seed must be a non-negative integer")
        if not 4 <= self.n_samples_log2 <= 14:
            raise ValueError("n_samples_log2 must be between 4 and 14 (16 to 16384 samples)")
        if not self.parameters:
            raise ValueError("sensitivity needs at least one sampled parameter")
        ids = [p.variable_id for p in self.parameters]
        if len(ids) != len(set(ids)):
            raise ValueError("sensitivity parameters must be unique by variable_id")
        if self.target.variable_id in ids:
            raise ValueError("the target variable cannot also be a sampled parameter")
        return self


class BackfireSearchSpec(Frozen):
    """Automated backfire hunt: adaptive 1-D search of every listed parameter for the regions where the
    environmental benefit disappears or reverses (sign changes of the decision function ``g``; ``g < 0`` = beneficial).

    ``max_evaluations`` is mandatory (no default on purpose): the hunt never runs without an explicit budget. The
    other values are documented POLICY CHOICES (not derived from data); they are recorded in the result.
    """

    max_evaluations: int
    #: a reversal/benefit interval narrower than this fraction of the searched range is reported as ``narrow``
    narrow_fraction: float = 0.05
    #: |g| / reference-scale at or below this marks a near-break-even regime at the baseline (reporting only)
    near_break_even_tol: float = 0.01
    #: grid points of the local re-scan of a suspect cell pair (sub-grid reversal detection)
    refine_points: int = 17
    #: |g| / reference-scale at or below this at a refined extremum = a tangent touch (break-even without reversal)
    touch_tol: float = 1e-9
    #: share of the ranking score given to numerical well-definedness (the other three weights are scaled by 1 - w)
    well_defined_weight: float = 0.2
    #: upper bound on grid points of a candidate's local window (refined sub-grid candidates only)
    max_local_grid_points: int = 2001

    @model_validator(mode="after")
    def _check(self) -> BackfireSearchSpec:
        if isinstance(self.max_evaluations, bool) or self.max_evaluations < 1:
            raise ValueError("backfire max_evaluations must be an integer >= 1")
        for name in ("narrow_fraction", "near_break_even_tol", "touch_tol"):
            v = getattr(self, name)
            if not (math.isfinite(v) and v > 0):
                raise ValueError(f"backfire {name} must be positive and finite")
        if not (math.isfinite(self.well_defined_weight) and 0.0 <= self.well_defined_weight < 1.0):
            raise ValueError("backfire well_defined_weight must be in [0, 1)")
        if self.refine_points < 5 or self.max_local_grid_points < 3:
            raise ValueError("backfire refine_points must be >= 5 and max_local_grid_points >= 3")
        return self


class DiscoverySpec(Frozen):
    schema_version: str = DISCOVERY_SCHEMA_VERSION
    discovery_id: str
    title: str
    research_question: str
    model_ref: str
    options: dict[str, str] = Field(default_factory=dict)
    bindings: dict[str, Binding]
    #: model input -> evidence request (omitted from the serialized spec when empty; see ExperimentConfiguration).
    evidence_requests: dict[str, EvidenceRequest] = Field(default_factory=dict)
    decision_metric_ref: str
    parameters: list[ParameterRange] = Field(default_factory=list)
    regions: list[RegionSearch] = Field(default_factory=list)
    sensitivity: SensitivitySpec | None = None
    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    #: automated backfire hunt (omitted from the serialized spec when absent, so plain specs hash as before)
    backfire: BackfireSearchSpec | None = None

    @field_validator("model_ref")
    @classmethod
    def _exact_version(cls, v: str) -> str:
        if "@" not in v or v.endswith("@latest") or v.endswith("@"):
            raise ValueError("model_ref must be 'model_id@exact.version' (no 'latest')")
        return v

    @model_serializer(mode="wrap")
    def _omit_unused_evidence(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        return omit_empty(handler(self), "evidence_requests", "backfire")

    @field_validator("discovery_id")
    @classmethod
    def _id_ok(cls, v: str) -> str:
        if not v or any(c.isspace() or c in "/\\" for c in v):
            raise ValueError("discovery_id must be non-empty with no whitespace or path separators")
        return v

    @model_validator(mode="after")
    def _params(self) -> DiscoverySpec:
        if not self.parameters and not self.regions and self.sensitivity is None:
            raise ValueError("a discovery spec must list at least one parameter, region or sensitivity analysis")
        ids = [p.variable_id for p in self.parameters]
        if len(ids) != len(set(ids)):
            raise ValueError("parameters must be unique by variable_id")
        return self


# ---------------------------------------------------------------- results


class ScoreBreakdown(Frozen):
    components: dict[str, float]  # each in [0, 1]
    weights: dict[str, float]
    total: float
    formula: str


RejectionCode = Literal[
    "parameter_not_used_under_options",
    "no_sign_change_always_beneficial",
    "no_sign_change_never_beneficial",
    "no_sign_change_indeterminate",
    "no_reversal_in_region_always_beneficial",
    "no_reversal_in_region_never_beneficial",
    "no_reversal_in_region_indeterminate",
    "discontinuity_suspected",
    "root_not_converged",
    "evaluation_failed",
    "tangent_touch_no_reversal",
    "budget_exhausted",
]


class BackfireBaseline(Frozen):
    """The intervention measured against its own baseline at the declared base inputs (no new equation: every value
    is a model output, and ``g`` is the existing decision function ``sign-adjusted(metric output - threshold)``)."""

    metric_id: str
    output_id: str
    comparator: str
    threshold: float
    metric_output_value: float  # raw model output of the decision metric (e.g. dC, kgCO2e/yr)
    g_at_baseline: float  # decision function at the base inputs; < 0 beneficial
    reference_scale: float  # same scale the ranking uses (max(|C0|, |C1|, 1))
    relative_change: float | None  # metric output / baseline-state emissions C0 when the model reports C0
    regime: Literal["beneficial", "near_break_even", "adverse"]
    regime_tolerance: float  # near-break-even band as a fraction of reference_scale (policy)
    outputs: dict[str, float]  # every model output at the baseline
    inputs: dict[str, QuantityValue]
    chain: list[str]  # Baseline -> Intervention -> metric change -> search region -> candidates, in words


class BackfireInterval(Frozen):
    """One maximal sub-interval of a searched range on which the decision function has one sign."""

    low: float
    high: float
    regime: Literal["beneficial", "adverse", "indeterminate"]
    width_fraction: float  # of the searched range
    narrow: bool
    contains_baseline: bool
    lower_boundary: str  # candidate id, 'range_edge', or 'rejected_boundary'
    upper_boundary: str


class BackfireParameterReport(Frozen):
    variable_id: str
    unit: str
    low: float | None
    high: float | None
    grid_points: int
    range_basis: str
    status: Literal["searched", "budget_exhausted", "excluded", "failed"]
    behaviour: Literal[
        "remains_beneficial",
        "never_beneficial_in_range",
        "reverses",
        "touches_break_even",
        "not_searched",
    ]
    evaluations: int
    n_crossings_grid: int
    n_crossings_refined: int
    n_touches: int
    n_extrema_refined: int
    n_extrema_unrefined: int
    min_abs_g_over_scale: float | None  # closest approach of the scan to break-even
    intervals: list[BackfireInterval]
    note: str


class BackfireExclusion(Frozen):
    variable_id: str
    reason: str


class BackfireCandidateInfo(Frozen):
    """Everything needed to read one break-even candidate against the baseline (attached to the existing Candidate;
    there is no competing candidate format)."""

    origin: Literal["global_grid", "local_refinement"]
    model_ref: str
    objective: str
    baseline_metric_output: float
    baseline_g: float
    baseline_regime: Literal["beneficial", "near_break_even", "adverse"]
    metric_before: float | None  # g at the low end of the bracket
    metric_after: float | None  # g at the high end of the bracket
    transition: str  # in words: which side is beneficial
    estimated_breakeven: float
    estimate_uncertainty: float | None  # bracket width (an upper bound on the location error)
    distance_from_baseline: float  # value - base_value, canonical unit
    distance_relative_to_baseline: float | None  # / |base_value|, None if base_value == 0
    distance_range_normalized: float  # / width of the parameter's searched range
    baseline_on_beneficial_side: bool | None
    parameter_range: tuple[float, float, int]  # the parameter's full searched range and grid
    assumptions: dict[str, dict[str, object]]  # every other input held at base: value, unit, provenance class
    search_configuration: dict[str, object]


class BackfireReport(Frozen):
    status: Literal["complete", "budget_exhausted", "no_searchable_parameters"]
    objective: str
    baseline: BackfireBaseline
    max_evaluations: int
    evaluations_performed: int
    parameters: list[BackfireParameterReport]
    excluded: list[BackfireExclusion]
    n_parameters_searched: int
    n_candidates_found: int  # crossings turned into candidates
    n_candidates_from_refinement: int
    n_rejected: int  # rejected searches/roots recorded in DiscoveryResult.rejected
    n_reversal_regions: int  # bounded adverse intervals found
    n_narrow_reversal_regions: int
    policy: dict[str, object]  # every documented policy value used
    limitations: list[str]


class Candidate(Frozen):
    candidate_id: str
    rank: int
    kind: Literal["breakeven"] = "breakeven"
    label: Literal["candidate"] = "candidate"  # existing Label vocabulary: verification is incomplete
    variable_id: str
    unit: str  # canonical unit of the variable (values below are canonical)
    value: float
    root_kind: Literal["crossing", "grid_zero"]
    bracket_low: float | None
    bracket_high: float | None
    g_low: float | None
    g_high: float | None
    residual: float
    iterations: int
    beneficial_side: Literal["below", "above", "unknown"]
    search_low: float
    search_high: float
    grid_points: int
    base_value: float
    range_basis: str
    range_provenance: ProvenanceClass
    weakest_input_provenance: ProvenanceClass
    hypothesis: str
    score: ScoreBreakdown
    warnings: list[str]
    trace_at_root: list[dict[str, object]]
    #: Where every model input of this candidate came from (searched variable + inputs held at base).
    #: Empty (and omitted) for runs that requested no evidence.
    input_evidence: list[InputEvidenceRef] = Field(default_factory=list)
    #: backfire-hunt context (None, and omitted, for plain discovery)
    backfire: BackfireCandidateInfo | None = None

    @model_serializer(mode="wrap")
    def _omit_unused_evidence(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        return omit_empty(handler(self), "input_evidence", "backfire")


class RejectedCandidate(Frozen):
    variable_id: str
    code: RejectionCode
    detail: str
    search_low: float | None = None
    search_high: float | None = None
    value: float | None = None  # root value for rejected roots, if any


class RegionResult(Frozen):
    kind: Literal["reversal_region"] = "reversal_region"
    label: Literal["candidate"] = "candidate"
    x_variable_id: str
    y_variable_id: str
    x_unit: str
    y_unit: str
    x_low: float
    x_high: float
    y_low: float
    y_high: float
    nx: int
    ny: int
    n_beneficial: int
    n_not_beneficial: int
    n_zero: int
    fraction_not_beneficial: float
    boundary_points: list[tuple[float, float]]  # refined roots of the decision function, canonical units
    n_suspected_discontinuities: int
    base_x: float
    base_y: float
    base_inside_ranges: bool
    base_decision: Literal["beneficial", "not_beneficial", "indeterminate"]
    base_to_boundary_distance: float | None  # range-normalized; upper bound at grid resolution
    x_range_basis: str
    y_range_basis: str
    hypothesis: str
    warnings: list[str]
    #: Where every model input of this region search came from (empty and omitted without evidence requests).
    input_evidence: list[InputEvidenceRef] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _omit_unused_evidence(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        return omit_empty(handler(self), "input_evidence")


class SensitivityRow(Frozen):
    rank: int
    variable_id: str
    unit: str
    low: float
    high: float
    rank_correlation: float | None  # Spearman rho with the break-even location; None if undefined
    abs_rank_correlation: float | None


class SensitivityResult(Frozen):
    target_variable_id: str
    target_unit: str
    target_low: float
    target_high: float
    method: str
    seed: int
    n_samples: int
    n_failed: int
    n_with_breakeven: int
    fraction_with_breakeven: float  # of non-failed samples: share of the declared input box where a break-even exists
    target_quantiles: dict[str, float]
    rows: list[SensitivityRow]
    skipped: list[str]
    scipy_version: str
    limitations: list[str]
    input_evidence: list[InputEvidenceRef] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _omit_unused_evidence(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        return omit_empty(handler(self), "input_evidence")


class DiscoveryResult(Frozen):
    schema_version: str = DISCOVERY_SCHEMA_VERSION
    discovery_id: str
    spec_sha256: str
    model_ref: str
    options: dict[str, str]
    system_boundary: SystemBoundary
    functional_unit: FunctionalUnit
    decision_metric_ref: str
    base_inputs: dict[str, QuantityValue]
    weakest_input_provenance: ProvenanceClass
    candidates: list[Candidate]
    regions: list[RegionResult] = Field(default_factory=list)
    sensitivity: SensitivityResult | None = None
    rejected: list[RejectedCandidate]
    scoring_policy: dict[str, object]
    criteria_not_yet_applied: list[str]
    status_note: str
    warnings: list[str]
    evidence_resolution: EvidenceResolutionSummary | None = None
    backfire: BackfireReport | None = None

    @model_serializer(mode="wrap")
    def _omit_unused_evidence(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        return omit_empty(handler(self), "evidence_resolution", "backfire")
