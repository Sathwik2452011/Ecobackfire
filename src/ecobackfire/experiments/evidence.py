"""Materialize evidence requests into a configuration (layer 3).

``materialize_bindings`` is the single entry point every caller uses (experiment runner, verification, discovery,
CLI): it resolves the requests through ``datasets.resolution``, merges the resulting bindings, and returns

* the effective bindings (the SAME ``Binding`` type the engine always consumed - there is no second calculation
  path: ``prepare_inputs`` converts these bindings exactly like hand-written ones),
* ``verified_evidence``: input -> evidence id, which is what lets ``prepare_inputs`` accept a sourced binding,
* an ``EvidenceResolutionSummary`` listing where EVERY bound input comes from.

Configurations without evidence requests pass through untouched (no summary, no registry needed).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ecobackfire.contracts.experiment import Binding, ExperimentConfiguration
from ecobackfire.contracts.model import EnvironmentalModel
from ecobackfire.contracts.resolution import EvidenceRequest, EvidenceResolutionSummary
from ecobackfire.datasets.registry import DatasetRegistry
from ecobackfire.datasets.resolution import build_summary, resolve_parameter, resolve_requests
from ecobackfire.errors import ConfigError
from ecobackfire.models.registry import get_model


@dataclass(frozen=True)
class MaterializedEvidence:
    bindings: dict[str, Binding]
    verified_evidence: dict[str, str] = field(default_factory=dict)
    summary: EvidenceResolutionSummary | None = None


def materialize_bindings(
    spec: EnvironmentalModel,
    bindings: Mapping[str, Binding],
    requests: Mapping[str, EvidenceRequest],
    registry: DatasetRegistry | None,
) -> MaterializedEvidence:
    """Resolve ``requests``; raise ``MissingEvidenceError`` / ``EvidenceConflictError`` if any is unresolved."""
    if not requests:
        return MaterializedEvidence(dict(bindings))
    if registry is None:
        raise ConfigError(
            f"evidence requests for {sorted(requests)} need a dataset registry, but none was supplied "
            "(CLI: --registry DIR; API: registry=DatasetRegistry.from_directory(...))"
        )
    res = resolve_requests(registry, spec, bindings, requests)
    res.raise_if_unresolved()
    summary = build_summary(registry, res.bindings, res.resolutions)
    return MaterializedEvidence(res.bindings, res.verified_evidence, summary)


def materialize_config(
    config: ExperimentConfiguration, registry: DatasetRegistry | None, model_spec: EnvironmentalModel | None = None
) -> tuple[ExperimentConfiguration, MaterializedEvidence]:
    """The effective configuration (requests + resolved bindings) and its evidence. Idempotent."""
    if not config.evidence_requests:
        return config, MaterializedEvidence(dict(config.bindings))
    spec = model_spec or get_model(config.model_ref).spec
    mat = materialize_bindings(spec, config.bindings, config.evidence_requests, registry)
    return config.model_copy(update={"bindings": mat.bindings}), mat


def alternative_configs(
    config: ExperimentConfiguration,
    registry: DatasetRegistry,
    variable_id: str,
    model_spec: EnvironmentalModel | None = None,
) -> dict[str, ExperimentConfiguration]:
    """One pinned configuration per applicable record of ``variable_id`` (use after a conflict, or to evaluate
    every source). Each is the original request with ``select`` set; nothing is chosen on the caller's behalf."""
    req = config.evidence_requests.get(variable_id)
    if req is None:
        raise ConfigError(f"{variable_id!r} has no evidence request in this configuration")
    spec = model_spec or get_model(config.model_ref).spec
    res = resolve_parameter(registry, spec, variable_id, req.model_copy(update={"select": None}))
    out: dict[str, ExperimentConfiguration] = {}
    for rec in res.applicable:
        pinned = req.model_copy(update={"select": rec.evidence_id})
        reqs = {**config.evidence_requests, variable_id: pinned}
        stripped = {k: v for k, v in config.bindings.items() if k != variable_id}
        out[rec.evidence_id] = config.model_copy(update={"evidence_requests": reqs, "bindings": stripped})
    return out
