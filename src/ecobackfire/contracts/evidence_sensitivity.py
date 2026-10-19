"""Evidence-sensitive evaluation contracts (layer 0).

What this layer says, and what it does not
------------------------------------------
A candidate is a *modeled* finding: "under model M and these inputs, decision metric D changes sign at x*".
Evidence-sensitive evaluation re-runs that model under the *documented* evidence variation that exists for its
inputs and reports whether the modeled finding is still present. Nothing here says the finding is true of the
real world.

* An ``EvidenceRange`` is an interval a SOURCE documents (lower / upper, and a central value when the record
  states one). It is not a probability distribution and no distribution is inferred from it.
* An ``EvidenceScenario`` is one concrete input value to evaluate: a source's value, or a documented bound.
* A source alternative is another credible record for the same input. Alternatives are evaluated side by side;
  none is preferred because it gives a more interesting result.
* "Evidence robustness" answers: *does the modeled finding remain present across the documented evidence range
  or alternative credible sources?* It is not confidence and not a probability.

Nothing here validates against a registry; building scenarios from a resolution is
``discovery/evidence_scenarios.py``.
"""

from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import Field, model_validator

from ecobackfire.contracts.common import Frozen

EVIDENCE_SENSITIVITY_SCHEMA_VERSION = "1.0.0"

#: ``source``: one record's single value. ``lower`` / ``central`` / ``upper``: a record that documents an interval.
ScenarioRole = Literal["source", "lower", "central", "upper"]

#: What happened to the candidate (a break-even of the same beneficial side) in one scenario.
OutcomeKind = Literal[
    "preserved",
    "preserved_near_search_edge",  # a root exists but sits within ``edge_margin`` of the search window: unresolved
    "reversed",  # a crossing exists but the beneficial side flipped: the finding changes meaning
    "disappeared_always_beneficial",
    "disappeared_never_beneficial",
    "disappeared_other",
    "invalid",  # the model could not be evaluated (e.g. out of physical bounds): unresolved, never a pass
]
PRESERVED_KINDS: frozenset[str] = frozenset({"preserved"})
LOST_KINDS: frozenset[str] = frozenset(
    {"reversed", "disappeared_always_beneficial", "disappeared_never_beneficial", "disappeared_other"}
)
UNRESOLVED_KINDS: frozenset[str] = frozenset({"preserved_near_search_edge", "invalid"})

#: The four kinds of challenge, reported separately and never merged into one score.
ChallengeType = Literal[
    "numerical_falsification",  # X1-X4, X6: is the arithmetic/root real, stable, reproducible, independently found?
    "parameter_perturbation",  # X5 and configured/user ranges: one-at-a-time perturbation, NOT documented evidence
    "evidence_range_sensitivity",  # documented lower/central/upper of a record
    "source_disagreement",  # alternative credible records for the same input
]
CHALLENGE_TYPES: tuple[str, ...] = (
    "numerical_falsification",
    "parameter_perturbation",
    "evidence_range_sensitivity",
    "source_disagreement",
)

CandidateClass = Literal[
    # numerical / parameter axis (vocabulary planned in the falsification module; 'refuted' is the existing Label)
    "refuted",
    "numerically_unstable",
    "not_reproduced",
    "boundary_dependent",
    "sensitive",
    # evidence axis
    "eliminated_by_evidence",
    "single_source_dependent",
    "evidence_boundary_dependent",
    "evidence_sensitive",
    "evidence_insufficient",
    # combined outcomes
    "inconclusive",
    "robust",
]
EvidenceClass = Literal[
    "evidence_robust",
    "eliminated_by_evidence",
    "single_source_dependent",
    "evidence_boundary_dependent",
    "evidence_sensitive",
    "evidence_insufficient",
    "evidence_inconclusive",
]
NumericalClass = Literal[
    "refuted",
    "numerically_unstable",
    "not_reproduced",
    "boundary_dependent",
    "sensitive",
    "inconclusive",
    "robust",
]
DimensionName = Literal["numerical", "parameter", "evidence"]
DimensionStatus = Literal["held", "not_held", "inconclusive", "not_assessed"]

DIMENSION_QUESTIONS: dict[str, str] = {
    "numerical": "Is the break-even a real, numerically stable, reproducible sign change of the model's decision "
    "function (X1-X4, X6, search-window dependence)?",
    "parameter": "Does the modeled finding remain present under configured / user-chosen one-at-a-time parameter "
    "perturbations that are NOT documented evidence (X5 and robustness-analysis ranges)?",
    "evidence": "Does the modeled finding remain present across the documented evidence range or alternative "
    "credible sources?",
}

_ID_RE = re.compile(r"^[^\s]+$")


class EvidenceRange(Frozen):
    """A documented interval supplied by one source for one model input (canonical unit).

    ``distribution_implied`` is always False: a documented range is an interval, not a distribution, and no
    probability is attached to any point in it.
    """

    parameter: str
    unit: str  # the model's canonical unit (values below are canonical)
    lower: float
    upper: float
    central: float | None = None  # the record's own value, when the record states an interval AND a value inside it
    evidence_id: str
    source_id: str | None = None
    dataset_id: str
    dataset_version: str
    geography: str | None = None
    period: str | None = None
    provenance_class: str
    kind: Literal["documented_interval"] = "documented_interval"
    distribution_implied: Literal[False] = False

    @model_validator(mode="after")
    def _ordered(self) -> EvidenceRange:
        vals = [self.lower, self.upper] + ([self.central] if self.central is not None else [])
        if not all(math.isfinite(v) for v in vals):
            raise ValueError("evidence range values must be finite")
        if not self.lower < self.upper:
            raise ValueError(
                f"a documented range needs lower < upper, got [{self.lower}, {self.upper}] "
                "(a degenerate interval is a point, not a range)"
            )
        if self.central is not None and not self.lower <= self.central <= self.upper:
            raise ValueError(f"central {self.central} lies outside the documented range [{self.lower}, {self.upper}]")
        return self


class EvidenceScenario(Frozen):
    """One concrete input value to evaluate, with the complete chain back to its record."""

    scenario_id: str  # "<parameter>|<evidence_id>|<role>": deterministic, unique within a candidate evaluation
    parameter: str
    role: ScenarioRole
    value: float  # canonical unit
    unit: str  # canonical unit
    original_value: float | None = None  # in the record's own unit (for the range endpoints: the bound as stated)
    original_unit: str | None = None
    evidence_id: str
    source_id: str | None = None
    source_key: str  # source_id when present, else evidence_id: two records with the same key are ONE source
    dataset_id: str
    dataset_version: str
    geography: str | None = None
    period: str | None = None
    provenance_class: str
    status: str
    release_content_sha256: str | None = None
    is_baseline: bool = False  # True when this is the value the candidate was computed with

    @model_validator(mode="after")
    def _finite(self) -> EvidenceScenario:
        if not math.isfinite(self.value):
            raise ValueError("scenario value must be finite")
        if not _ID_RE.match(self.scenario_id):
            raise ValueError("scenario_id must not contain whitespace")
        return self


class EvidenceScenarioSet(Frozen):
    """Every documented value/range/alternative that exists for ONE model input."""

    parameter: str
    unit: str
    #: Resolution state the scenarios were built from: resolved | conflicting | missing.
    resolution_state: Literal["resolved", "conflicting", "missing"]
    resolution_decision: str  # the selection rule, or why none was applied
    baseline_evidence_id: str | None
    scenarios: list[EvidenceScenario] = Field(default_factory=list)
    ranges: list[EvidenceRange] = Field(default_factory=list)
    source_keys: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)  # "<evidence id>: <reason>", records NOT evaluated and why
    warnings: list[str] = Field(default_factory=list)

    @property
    def n_sources(self) -> int:
        return len(self.source_keys)

    @model_validator(mode="after")
    def _consistent(self) -> EvidenceScenarioSet:
        ids = [s.scenario_id for s in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("scenario ids must be unique within a scenario set")
        if any(s.parameter != self.parameter for s in self.scenarios):
            raise ValueError("every scenario in a set must belong to the set's parameter")
        if self.resolution_state == "missing" and self.scenarios:
            raise ValueError("a missing resolution cannot carry scenarios")
        return self


class ScenarioOutcome(Frozen):
    """The model result for one scenario."""

    scenario_id: str
    parameter: str
    role: ScenarioRole
    evidence_id: str
    source_key: str
    dataset_id: str
    dataset_version: str
    value: float
    unit: str
    outcome: OutcomeKind
    #: True when the scenario value equals the base input the candidate was computed with: it reproduces the
    #: candidate rather than challenging it, and does not count as variation for the evidence_insufficient rule.
    is_baseline: bool = False
    root: float | None
    beneficial_side: str | None
    root_shift_rel: float | None
    edge_margin: float | None
    n_roots: int
    reason: str
    challenge_type: Literal["evidence_range_sensitivity", "source_disagreement"]

    @property
    def preserved(self) -> bool:
        return self.outcome in PRESERVED_KINDS

    @property
    def lost(self) -> bool:
        return self.outcome in LOST_KINDS


class ChallengeResult(Frozen):
    """One challenge applied to a candidate, tagged with its TYPE so a failure is never an unexplained score."""

    challenge_type: ChallengeType
    check_id: str
    status: Literal["passed", "failed", "limits_claim", "inconclusive", "skipped"]
    category: str
    summary: str
    scenario_ids: list[str] = Field(default_factory=list)  # evidence challenges: the scenarios behind the verdict


class RobustnessDimension(Frozen):
    """One explicitly identified robustness dimension. The three dimensions are never averaged together."""

    dimension: DimensionName
    question: str
    status: DimensionStatus
    n_cases: int  # how many cases informed this dimension (0 when not assessed)
    n_preserved: int
    n_lost: int
    n_unresolved: int
    detail: str
    basis: str  # what supplied the variation: e.g. "documented evidence ranges and alternative sources"


class ClassificationDecision(Frozen):
    """Why a candidate received its classification. Rule identifiers refer to ``RULE_DEFINITIONS``."""

    numerical_class: NumericalClass
    evidence_class: EvidenceClass
    classification: CandidateClass
    rule_id: str
    reasons: list[str]
    failed_challenge_types: list[ChallengeType]
    limiting_challenge_types: list[ChallengeType]  # challenges with status limits_claim
    not_assessed: list[str]


class EvidenceSensitivityConfig(Frozen):
    """Every setting that influences an evidence-sensitive evaluation. Recorded verbatim; no hidden defaults.

    ``edge_margin`` is a policy choice (fraction of the search width within which a root counts as "near the
    edge of the search window" and therefore unresolved). It is not derived from data.
    """

    edge_margin: float = 0.02
    repeats: int = 2  # whole evaluation is repeated and compared bit-for-bit (determinism check)
    max_scenarios: int = 500  # the evaluation is bounded; exceeding it is a configuration error, not truncation

    @model_validator(mode="after")
    def _ok(self) -> EvidenceSensitivityConfig:
        if not (math.isfinite(self.edge_margin) and 0 <= self.edge_margin < 0.5):
            raise ValueError("edge_margin must be in [0, 0.5)")
        if self.repeats < 2:
            raise ValueError("repeats must be >= 2")
        if self.max_scenarios < 1:
            raise ValueError("max_scenarios must be >= 1")
        return self


class CandidateEvidenceEvaluation(Frozen):
    """The complete, reproducible chain for one candidate (item 7 of the brief)."""

    schema_version: str = EVIDENCE_SENSITIVITY_SCHEMA_VERSION
    candidate_id: str
    model_ref: str
    variable_id: str  # the searched input (whose break-even is the candidate)
    unit: str
    root: float
    beneficial_side: str
    search_low: float
    search_high: float
    config: EvidenceSensitivityConfig
    scenario_sets: list[EvidenceScenarioSet]
    outcomes: list[ScenarioOutcome]
    challenges: list[ChallengeResult]
    dimensions: list[RobustnessDimension]
    decision: ClassificationDecision
    deterministic: bool
    falsification_report_sha256: str | None = None
    robustness_report_sha256: str | None = None
    falsification_settings: dict[str, object] = Field(default_factory=dict)
    robustness_settings: dict[str, object] = Field(default_factory=dict)
    limitations: list[str]
    interpretation: str

    def outcomes_for(self, parameter: str) -> list[ScenarioOutcome]:
        return [o for o in self.outcomes if o.parameter == parameter]


RULE_DEFINITIONS: dict[str, str] = {
    # numerical axis (precedence order; the first rule that applies wins)
    "N1": "any failed X1 (crossing validity) -> numerical_class 'refuted'",
    "N2": "any failed X2 (numerical stability) -> 'numerically_unstable'",
    "N3": "any failed X4 (reproducibility) or X6 (independent roots) -> 'not_reproduced'",
    "N4": "any failed X3 (search-window dependence) -> 'boundary_dependent'",
    "N5": "any failed X5 / R1 (parameter perturbation), or an X5/R1/X3 limits_claim -> 'sensitive'",
    "N6": "X1-X4 not all passed, or parameter perturbation not assessed (X5 and R1 both skipped / inconclusive) "
    "-> 'inconclusive'",
    "N7": "X1-X4 passed and X5 or R1 passed -> 'robust' (X6 may be skipped; this is reported as not assessed)",
    # evidence axis
    "E0": "no documented evidence scenario other than the candidate's own base value exists (no range and no "
    "alternative source) -> 'evidence_insufficient'",
    "E1": "every evaluated scenario preserved the candidate -> 'evidence_robust'",
    "E2": "no scenario lost the candidate but at least one is unresolved (invalid / near the search edge) "
    "-> 'evidence_inconclusive'",
    "E3": "no scenario preserved the candidate and none is unresolved -> 'eliminated_by_evidence'",
    "E3b": "no scenario preserved the candidate but some are unresolved -> 'evidence_inconclusive'",
    "E4": "preserved only at documented range endpoints (lower/upper) and lost at every non-endpoint scenario "
    "-> 'evidence_boundary_dependent'",
    "E5": "two or more sources were evaluated, exactly one source has preserved scenarios and every other "
    "source has none -> 'single_source_dependent'",
    "E6": "scenarios disagree and neither E4 nor E5 applies -> 'evidence_sensitive'",
    # combination (first applicable)
    "C1": "numerical_class in {refuted, numerically_unstable, not_reproduced}: arithmetic/numerics decide; evidence "
    "variation of a finding that is not a real stable crossing is not interpreted",
    "C2": "numerical_class 'boundary_dependent' -> 'boundary_dependent'",
    "C3": "evidence_class in {eliminated_by_evidence, single_source_dependent, evidence_boundary_dependent, "
    "evidence_sensitive} -> that class (evidence outranks parameter sensitivity: it is documented variation)",
    "C4": "numerical_class 'sensitive' -> 'sensitive'",
    "C5": "numerical_class 'inconclusive' or evidence_class 'evidence_inconclusive' -> 'inconclusive'",
    "C6": "evidence_class 'evidence_insufficient' -> 'evidence_insufficient' (cannot be called robust)",
    "C7": "numerical_class 'robust' and evidence_class 'evidence_robust' -> 'robust'",
}

LIMITATIONS: tuple[str, ...] = (
    "A modeled finding that survives is not thereby a statement about the real world; it is a statement about "
    "this model under these documented inputs.",
    "Evidence scenarios are evaluated one input at a time; interactions between inputs that each have documented "
    "variation are not tested.",
    "Only the documented points (source values, lower/central/upper bounds) are evaluated. A documented range is "
    "an interval, not a distribution; behaviour between the points is not assumed.",
    "Inputs without documented evidence (assumed or illustrative) are held at their declared values and are not "
    "varied by the evidence challenge; they remain unresolved limitations of the finding.",
    "The classification rules are categorical and policy-defined (RULE_DEFINITIONS); they contain no probability "
    "or confidence value.",
    "Model-structure uncertainty (boundary, demand form, equations) is outside evidence sensitivity.",
)

INTERPRETATION = (
    "The model produces this result under these documented inputs. This does not establish that the real world "
    "behaves this way."
)


class DatasetUse(Frozen):
    """One dataset release an evidence-sensitive evaluation drew scenarios from (exact version + content hash)."""

    dataset_id: str
    version: str
    release_content_sha256: str | None = None


class EvidenceSensitivityManifest(Frozen):
    """Everything needed to reproduce an evidence-sensitive evaluation, with NO clock, host or path in it.

    ``inputs_sha256`` covers what was asked (model, search windows, scenario definitions incl. ranges and
    alternative-source selections, resolution decisions, falsification/robustness/evidence settings and the
    classification rules). ``results_sha256`` covers what came out (outcomes, challenges, dimensions,
    classifications). Identical ``inputs_sha256`` must give an equivalent ``results_sha256``.
    """

    schema_version: str = EVIDENCE_SENSITIVITY_SCHEMA_VERSION
    label: str
    model_ref: str
    datasets: list[DatasetUse]
    source_keys: list[str]
    classification_rules: dict[str, str]
    config: EvidenceSensitivityConfig
    evaluations: list[CandidateEvidenceEvaluation]
    inputs_sha256: str
    results_sha256: str
    note: str = (
        "Scientific content only: no timestamp, host or path. The results describe a modeled finding under the "
        "documented inputs listed; they are not statements about the real world."
    )
