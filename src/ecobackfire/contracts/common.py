"""Shared building blocks (layer 0). Immutable, strict pydantic models."""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ILLUSTRATIVE_MARKER = "ILLUSTRATIVE — not evidence"


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProvenanceClass(StrEnum):
    MEASURED = "measured"
    SOURCED = "sourced"
    ESTIMATED = "estimated"
    SIMULATED = "simulated"
    USER_SUPPLIED = "user_supplied"
    ILLUSTRATIVE = "illustrative"


# Strongest to weakest (policy table; see docs/data-contracts.md section 1).
PROVENANCE_STRENGTH_ORDER: tuple[ProvenanceClass, ...] = (
    ProvenanceClass.MEASURED,
    ProvenanceClass.SOURCED,
    ProvenanceClass.ESTIMATED,
    ProvenanceClass.SIMULATED,
    ProvenanceClass.USER_SUPPLIED,
    ProvenanceClass.ILLUSTRATIVE,
)


def weakest_provenance(classes: list[ProvenanceClass]) -> ProvenanceClass | None:
    if not classes:
        return None
    return max(classes, key=PROVENANCE_STRENGTH_ORDER.index)


class PointUncertainty(Frozen):
    """Exact by declaration (a definition or a scenario choice). NOT 'unknown'."""

    kind: Literal["point"] = "point"


class UnknownUncertainty(Frozen):
    """Uncertainty not characterized. Distinct from 'point' so absence is never certainty."""

    kind: Literal["unknown"] = "unknown"


class IntervalUncertainty(Frozen):
    """Bounds only; no distribution is implied."""

    kind: Literal["interval"] = "interval"
    low: float
    high: float

    @model_validator(mode="after")
    def _ordered(self) -> IntervalUncertainty:
        if not (math.isfinite(self.low) and math.isfinite(self.high)):
            raise ValueError("interval bounds must be finite")
        if self.low > self.high:
            raise ValueError("interval requires low <= high")
        return self


Uncertainty = Annotated[PointUncertainty | UnknownUncertainty | IntervalUncertainty, Field(discriminator="kind")]


class FunctionalUnit(Frozen):
    id: str
    description: str
    quantity_unit: str
    reference_flow_description: str


class SystemBoundary(Frozen):
    id: str
    description: str
    included_stages: list[str]
    excluded_stages: list[str]
    cutoff_rules: str = "none declared"

    @model_validator(mode="after")
    def _disjoint(self) -> SystemBoundary:
        both = set(self.included_stages) & set(self.excluded_stages)
        if both:
            raise ValueError(f"stages both included and excluded: {sorted(both)}")
        return self


class ValidationIssue(Frozen):
    severity: Literal["error", "warning", "info"]
    tier: Literal["schema", "physical", "applicability"]
    code: str
    message: str
    path: str = ""


class QuantityValue(Frozen):
    value: float
    unit: str
