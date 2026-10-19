"""EnvironmentalVariable, ModelAssumptions, EnvironmentalModel (layer 0)."""

from __future__ import annotations

from typing import Literal

from pydantic import field_validator, model_validator

from ecobackfire.contracts.common import Frozen, FunctionalUnit, SystemBoundary


class PhysicalBounds(Frozen):
    min: float | None = None
    max: float | None = None
    inclusive_min: bool = True
    inclusive_max: bool = True


class EnvironmentalVariable(Frozen):
    id: str
    name: str
    description: str
    dimension: str  # human-readable label, e.g. "energy/time"; unit checks use canonical_unit
    canonical_unit: str
    kind: Literal["parameter", "state", "output", "metric"]
    physical_bounds: PhysicalBounds | None = None
    quantity_kind: str | None = None

    @field_validator("id")
    @classmethod
    def _id_ok(cls, v: str) -> str:
        if not v or any(c.isspace() for c in v):
            raise ValueError("variable id must be non-empty and contain no whitespace")
        return v


class Assumption(Frozen):
    id: str
    statement: str
    rationale: str
    testable: bool = False
    test_ref: str | None = None


class ModelAssumptions(Frozen):
    assumptions: list[Assumption]
    exclusions: list[str]
    functional_unit: FunctionalUnit
    system_boundary: SystemBoundary
    #: Model-declared validity ranges per input (low, high), canonical units. These are
    #: modelling-judgement placeholders, not evidence; leaving them is an applicability WARNING.
    applicability: dict[str, tuple[float, float]]
    known_limitations: list[str]

    @model_validator(mode="after")
    def _non_empty(self) -> ModelAssumptions:
        if not self.assumptions:
            raise ValueError("a model must declare at least one assumption")
        if not self.exclusions:
            raise ValueError("exclusions must be listed (use ['none declared'] explicitly)")
        return self


class DecisionMetric(Frozen):
    id: str
    output_id: str
    comparator: Literal["lt", "gt"]
    threshold: float
    direction_text: str


class Equation(Frozen):
    id: str
    text: str


class EnvironmentalModel(Frozen):
    schema_version: str = "1.0.0"
    model_id: str
    version: str
    title: str
    description: str
    domain: str
    inputs: list[EnvironmentalVariable]
    outputs: list[EnvironmentalVariable]
    options: dict[str, list[str]]  # categorical model options and their allowed values
    assumptions: ModelAssumptions
    decision_metrics: list[DecisionMetric]
    equations: list[Equation]
    status: Literal["draft", "reviewed", "deprecated"] = "draft"

    @model_validator(mode="after")
    def _unique_ids(self) -> EnvironmentalModel:
        ids = [v.id for v in self.inputs]
        if len(ids) != len(set(ids)):
            raise ValueError("input ids must be unique")
        return self

    @property
    def ref(self) -> str:
        return f"{self.model_id}@{self.version}"

    def input_by_id(self, vid: str) -> EnvironmentalVariable:
        for v in self.inputs:
            if v.id == vid:
                return v
        raise KeyError(vid)
