"""Resolve a ParameterRange to canonical-unit bounds, without ever inventing a range."""

from __future__ import annotations

from ecobackfire.contracts.common import IntervalUncertainty, ProvenanceClass
from ecobackfire.contracts.discovery import DiscoverySpec, ParameterRange
from ecobackfire.core.model import Model
from ecobackfire.errors import ConfigError
from ecobackfire.units import to_canonical


def resolve_range(
    spec: DiscoverySpec, model: Model, pr: ParameterRange
) -> tuple[float, float, ProvenanceClass, str]:
    """Return (low, high) in canonical units, the range's provenance class, and its basis text."""
    b = spec.bindings.get(pr.variable_id)
    if b is None:
        raise ConfigError(f"parameter {pr.variable_id!r} has no binding; its unit and base value are unknown")
    canon = model.spec.input_by_id(pr.variable_id).canonical_unit
    if pr.low is not None and pr.high is not None:
        lo_u, hi_u, prov = pr.low, pr.high, pr.provenance_class
    elif isinstance(b.uncertainty, IntervalUncertainty):
        lo_u, hi_u, prov = b.uncertainty.low, b.uncertainty.high, b.provenance_class
        if not lo_u < hi_u:
            raise ConfigError(f"{pr.variable_id}: binding interval is degenerate [{lo_u}, {hi_u}]; give a range")
    else:
        raise ConfigError(
            f"{pr.variable_id}: no explicit low/high and the binding has no interval uncertainty "
            f"(kind={b.uncertainty.kind!r}); the engine never invents a range"
        )
    return (
        to_canonical(lo_u, b.unit, canon, what=pr.variable_id),
        to_canonical(hi_u, b.unit, canon, what=pr.variable_id),
        prov,
        pr.basis,
    )
