"""Input preparation: unit conversion, missing-data refusal, bounds, applicability warnings."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from ecobackfire.contracts.common import ProvenanceClass
from ecobackfire.contracts.experiment import Binding
from ecobackfire.contracts.model import EnvironmentalModel
from ecobackfire.errors import (
    ConfigError,
    EvidenceNotAvailableError,
    MissingEvidenceError,
    PhysicalConstraintError,
)
from ecobackfire.units import to_canonical


@dataclass
class PreparedInputs:
    values: dict[str, float]  # canonical units
    warnings: list[str] = field(default_factory=list)


def check_physical_bounds(spec: EnvironmentalModel, values: Mapping[str, float]) -> None:
    """Raise PhysicalConstraintError on any NaN/inf or declared-bound violation."""
    for vid, x in values.items():
        if not isinstance(x, (int, float)) or not math.isfinite(x):
            raise PhysicalConstraintError(f"{vid}: value must be finite, got {x!r}")
        var = spec.input_by_id(vid)
        b = var.physical_bounds
        if b is None:
            continue
        if b.min is not None and (x < b.min or (x == b.min and not b.inclusive_min)):
            raise PhysicalConstraintError(
                f"{vid}={x} violates lower bound {b.min} ({'inclusive' if b.inclusive_min else 'exclusive'})"
            )
        if b.max is not None and (x > b.max or (x == b.max and not b.inclusive_max)):
            raise PhysicalConstraintError(
                f"{vid}={x} violates upper bound {b.max} ({'inclusive' if b.inclusive_max else 'exclusive'})"
            )


def applicability_warnings(spec: EnvironmentalModel, values: Mapping[str, float]) -> list[str]:
    out: list[str] = []
    for vid, (lo, hi) in spec.assumptions.applicability.items():
        if vid in values and not (lo <= values[vid] <= hi):
            out.append(
                f"applicability: {vid}={values[vid]:g} is outside the model's declared validity "
                f"range [{lo:g}, {hi:g}] (extrapolation; results are exploratory)"
            )
    return out


def prepare_inputs(
    spec: EnvironmentalModel,
    bindings: Mapping[str, Binding],
    required: Iterable[str],
    *,
    verified_evidence: Mapping[str, str] | None = None,
) -> PreparedInputs:
    """Validate bindings and convert them to canonical units.

    ``verified_evidence`` maps input id -> evidence id for bindings whose evidence chain the CALLER has already
    verified against a dataset registry (``ecobackfire.datasets.resolution``). A measured/sourced binding is
    accepted only if it appears there with the same evidence id; otherwise it is refused exactly as before,
    because a TOML file that merely claims 'sourced' proves nothing.
    """
    verified = dict(verified_evidence or {})
    known = {v.id for v in spec.inputs}
    unknown = sorted(set(bindings) - known)
    if unknown:
        raise ConfigError(f"bindings for unknown inputs: {unknown}; model inputs are {sorted(known)}")

    for vid, b in bindings.items():
        if b.provenance_class in (ProvenanceClass.MEASURED, ProvenanceClass.SOURCED):
            if b.evidence_record_id is not None and verified.get(vid) == b.evidence_record_id:
                continue
            raise EvidenceNotAvailableError(
                f"{vid}: class '{b.provenance_class.value}' requires an evidence record "
                f"({b.evidence_record_id!r}) that has been resolved against a dataset registry. Declare an "
                f"[evidence_requests.{vid}] entry and supply the registry, or use 'illustrative' or "
                "'user_supplied'. A binding that only claims to be sourced is refused."
            )

    values: dict[str, float] = {}
    for vid in required:
        req = bindings.get(vid)
        if req is None:
            raise MissingEvidenceError(f"required input {vid!r} is not bound; no default is assumed")
        if req.missing:
            raise MissingEvidenceError(f"required input {vid!r} is explicitly marked missing")
    for vid, b in bindings.items():
        if b.missing:
            continue
        assert b.value is not None
        var = spec.input_by_id(vid)
        values[vid] = to_canonical(b.value, b.unit, var.canonical_unit, what=vid)
    check_physical_bounds(spec, values)
    return PreparedInputs(values=values, warnings=applicability_warnings(spec, values))
