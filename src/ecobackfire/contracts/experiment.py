"""Binding, ExperimentConfiguration, ExperimentResult (layer 0)."""

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
    Uncertainty,
    UnknownUncertainty,
)
from ecobackfire.contracts.resolution import EvidenceRequest, EvidenceResolutionSummary, omit_empty


class Binding(Frozen):
    """A value bound to a model input, with provenance. ``missing=True`` is an explicit gap."""

    value: float | None = None
    missing: bool = False
    unit: str
    uncertainty: Uncertainty = Field(default_factory=UnknownUncertainty)
    provenance_class: ProvenanceClass
    evidence_record_id: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Binding:
        if self.missing:
            if self.value is not None:
                raise ValueError("a binding marked missing must not carry a value")
        elif self.value is None or not math.isfinite(self.value):
            raise ValueError("binding value must be a finite number (or set missing=true)")
        if self.provenance_class in (ProvenanceClass.MEASURED, ProvenanceClass.SOURCED) and not self.evidence_record_id:
            raise ValueError("measured/sourced bindings require evidence_record_id")
        if self.provenance_class is ProvenanceClass.ILLUSTRATIVE and (
            not self.note or ILLUSTRATIVE_MARKER not in self.note
        ):
            raise ValueError(f"illustrative bindings must carry the note {ILLUSTRATIVE_MARKER!r}")
        return self


class Scenario(Frozen):
    name: str
    overrides: dict[str, float]  # variable id -> value, expressed in that binding's own unit


class SearchSpec(Frozen):
    variable_id: str
    low: float
    high: float
    grid_points: int = 201
    xtol: float = 1e-12
    #: Documented limitation: only linear grids are implemented in this phase.
    scale: Literal["linear"] = "linear"


class OATSpec(Frozen):
    target: Literal["delta_emissions", "breakeven"] = "delta_emissions"
    relative_perturbation: float = 0.2
    #: variable id -> (low, high) in that binding's unit. Overrides binding intervals.
    ranges: dict[str, tuple[float, float]] = Field(default_factory=dict)


class VerificationSettings(Frozen):
    """Red-team parameters. Numeric lists are ILLUSTRATIVE placeholders, not evidence."""

    perturbation_rel: float = 1e-3
    counterexample_points: int = 2000
    stability_grid_factor: int = 2
    alternative_s_max: list[float] = Field(default_factory=lambda: [1.05, 1.2, 1.5, 2.0, 3.0])
    agreement_rtol: float = 1e-9


class ExperimentConfiguration(Frozen):
    schema_version: str = "1.0.0"
    experiment_id: str
    title: str
    research_question: str
    model_ref: str
    kind: Literal["compare", "breakeven", "oat_sweep"]
    options: dict[str, str] = Field(default_factory=dict)
    bindings: dict[str, Binding]
    #: model input -> request to the dataset registry. Empty for configurations that do not use evidence
    #: (and then omitted from the serialized form, so their hash is unchanged).
    evidence_requests: dict[str, EvidenceRequest] = Field(default_factory=dict)
    scenarios: list[Scenario] = Field(default_factory=list)
    search: SearchSpec | None = None
    oat: OATSpec | None = None
    decision_metric_ref: str
    verification: VerificationSettings = Field(default_factory=VerificationSettings)

    @field_validator("model_ref")
    @classmethod
    def _exact_version(cls, v: str) -> str:
        if "@" not in v or v.endswith("@latest") or v.endswith("@"):
            raise ValueError("model_ref must be 'model_id@exact.version' (no 'latest')")
        return v

    @model_serializer(mode="wrap")
    def _omit_unused_evidence(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        return omit_empty(handler(self), "evidence_requests")

    @model_validator(mode="after")
    def _kind_requirements(self) -> ExperimentConfiguration:
        if self.kind == "breakeven" and self.search is None:
            raise ValueError("breakeven experiments require a [search] section")
        if self.kind == "oat_sweep" and self.oat is None:
            raise ValueError("oat_sweep experiments require an [oat] section")
        if self.kind == "oat_sweep" and self.oat is not None and self.oat.target == "breakeven" and self.search is None:
            raise ValueError("oat target 'breakeven' requires a [search] section")
        return self


# ---------------------------------------------------------------- results


class Decision(Frozen):
    metric_id: str
    value: float
    unit: str
    label: Literal["beneficial", "not_beneficial", "indeterminate"]


class ScenarioResult(Frozen):
    name: str
    overrides: dict[str, float]
    outputs: dict[str, QuantityValue]
    decision: Decision
    trace: list[dict[str, object]]


class BreakevenRoot(Frozen):
    value: float
    kind: Literal["crossing", "grid_zero", "discontinuity_suspected"]
    bracket_low: float | None
    bracket_high: float | None
    g_low: float | None
    g_high: float | None
    residual: float
    iterations: int
    converged: bool
    #: which side of the root has g < 0 (decision 'beneficial')
    beneficial_side: Literal["below", "above", "unknown"]


class BreakevenResult(Frozen):
    variable_id: str
    unit: str
    search_low: float
    search_high: float
    grid_points: int
    xtol: float
    classification: Literal[
        "crossing",
        "no_crossing_always_beneficial",
        "no_crossing_never_beneficial",
        "no_crossing_indeterminate",
    ]
    roots: list[BreakevenRoot]
    scan: list[tuple[float, float]]  # (x, g(x)) on the scan grid, canonical units
    note: str
    trace_at_first_root: list[dict[str, object]] = Field(default_factory=list)


class OATRow(Frozen):
    variable_id: str
    unit: str
    base_value: float
    low_value: float
    high_value: float
    range_source: Literal["config_range", "binding_interval", "relative_perturbation"]
    target_at_low: float | None
    target_at_high: float | None
    swing: float | None


class SensitivityResult(Frozen):
    target: str
    target_unit: str
    method: str
    base_target: float | None
    rows: list[OATRow]
    skipped: list[str]


class ProvenanceSummaryRow(Frozen):
    variable_id: str
    provenance_class: ProvenanceClass
    uncertainty_kind: str


class ExperimentResult(Frozen):
    schema_version: str = "1.0.0"
    experiment_id: str
    config_sha256: str
    model_ref: str
    kind: str
    status: Literal["completed", "completed_with_warnings"]
    options: dict[str, str]
    system_boundary: SystemBoundary
    functional_unit: FunctionalUnit
    base_inputs: dict[str, QuantityValue]
    compare: list[ScenarioResult] | None = None
    breakeven: BreakevenResult | None = None
    sensitivity: SensitivityResult | None = None
    input_provenance_summary: list[ProvenanceSummaryRow]
    weakest_provenance: ProvenanceClass
    warnings: list[str]
    #: Full evidence chain per input; present only when the configuration requested evidence.
    evidence_resolution: EvidenceResolutionSummary | None = None

    @model_serializer(mode="wrap")
    def _omit_unused_evidence(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        return omit_empty(handler(self), "evidence_resolution")

    @model_validator(mode="after")
    def _breakeven_has_bracket(self) -> ExperimentResult:
        if self.breakeven:
            for r in self.breakeven.roots:
                if r.kind == "crossing" and (r.g_low is None or r.g_high is None):
                    raise ValueError("a crossing root must record its bracket")
                if (
                    r.kind == "crossing"
                    and r.g_low is not None
                    and r.g_high is not None
                    and (r.g_low < 0) == (r.g_high < 0)
                ):
                    raise ValueError("bracket ends must have opposite decision signs")
        return self
