"""Glue: run discovery, then challenge every break-even candidate with evidence-sensitive evaluation (layer 4).

    DiscoverySpec (+ evidence_requests)
        -> ``run_discovery``                    existing engine, unchanged (resolves evidence, finds candidates)
        -> per candidate: ``evaluate_candidate_evidence``  (pure orchestration in ``evidence_sensitivity.py``)
              make_g  = the existing ``experiments.runner.make_g`` over the SAME model and options
              scenarios = every applicable record / documented range of every evidence-requested input
        -> ranking gated by robustness tier (``scoring.rank_key``)

Manifest and report are assembled one layer up (``api.evaluate_discovery_evidence``): discovery may not
import reporting.

There is no second calculation engine: the only model evaluation is ``model.evaluate`` through ``make_g``.

Conflicting evidence: ``run_discovery`` refuses a conflicting request unless the caller pins a baseline record
with ``EvidenceRequest.select`` (never silently chosen). Once pinned, EVERY other applicable record is still
evaluated as an alternative scenario.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ecobackfire.contracts.discovery import Candidate, DiscoveryResult, DiscoverySpec
from ecobackfire.contracts.evidence_sensitivity import (
    CandidateEvidenceEvaluation,
    EvidenceScenarioSet,
    EvidenceSensitivityConfig,
)
from ecobackfire.core.model import Model
from ecobackfire.datasets.registry import DatasetRegistry
from ecobackfire.discovery import scoring
from ecobackfire.discovery.engine import run_discovery
from ecobackfire.discovery.evidence_scenarios import build_scenario_set
from ecobackfire.discovery.evidence_sensitivity import evaluate_candidate_evidence
from ecobackfire.discovery.falsification import FalsificationConfig
from ecobackfire.discovery.robustness import PerturbationSpec, ReferenceRoots, RobustnessConfig
from ecobackfire.errors import ConfigError
from ecobackfire.experiments.breakeven import Root
from ecobackfire.experiments.runner import get_metric, make_g
from ecobackfire.models.registry import get_model


@dataclass(frozen=True)
class EvidenceDiscoveryOutcome:
    discovery: DiscoveryResult
    evaluations: list[CandidateEvidenceEvaluation]
    #: candidate ids in evidence-aware rank order (robustness tier first, then attention score)
    ranking: list[str]
    #: per-candidate three-axis scoring view (evidence documentation / validity not assessed / robustness)
    axes: dict[str, dict[str, object]]


def _root_of(c: Candidate) -> Root:
    return Root(
        value=c.value,
        kind=c.root_kind,
        bracket_low=c.bracket_low,
        bracket_high=c.bracket_high,
        g_low=c.g_low,
        g_high=c.g_high,
        residual=c.residual,
        iterations=c.iterations,
        converged=True,  # run_discovery only emits converged continuous crossings as candidates
        beneficial_side=c.beneficial_side,
    )


def evaluate_discovery_with_evidence(
    spec: DiscoverySpec,
    model: Model | None = None,
    *,
    registry: DatasetRegistry,
    config: EvidenceSensitivityConfig | None = None,
    falsification_config: FalsificationConfig | None = None,
    robustness_perturbations: Sequence[PerturbationSpec] = (),
    robustness_config: RobustnessConfig | None = None,
    reference_roots: ReferenceRoots | None = None,
    discovery_result: DiscoveryResult | None = None,
    candidate_ids: Sequence[str] | None = None,
) -> EvidenceDiscoveryOutcome:
    """Run discovery then evidence-sensitive evaluation of every candidate. Deterministic: no randomness.

    ``discovery_result`` (the result of ``run_discovery`` on this very spec and registry) lets an orchestrator run
    discovery as its own stage without computing it twice; ``candidate_ids`` restricts the evaluation to those
    candidates (the discovery result is still returned whole). Both default to the previous behaviour."""
    model = model or get_model(spec.model_ref)
    if not spec.evidence_requests:
        raise ConfigError(
            "evidence-sensitive evaluation needs at least one evidence request in the spec; without evidence there "
            "is nothing documented to vary (use plain discovery, or add [evidence_requests.<input>])"
        )
    cfg = config or EvidenceSensitivityConfig()
    result = discovery_result if discovery_result is not None else run_discovery(spec, model, registry=registry)
    summary = result.evidence_resolution
    assert summary is not None  # evidence requests were present, so a summary exists
    opts = result.options
    metric = get_metric(model, spec.decision_metric_ref)
    base = {k: q.value for k, q in result.base_inputs.items()}
    canon = {v.id: v.canonical_unit for v in model.spec.inputs}
    bounds = {v.id: v.physical_bounds for v in model.spec.inputs}
    rows = summary.by_variable()
    classes = [r.provenance_class.value for r in summary.inputs if r.provenance_class is not None]

    evaluations: list[CandidateEvidenceEvaluation] = []
    axes: dict[str, dict[str, object]] = {}
    wanted = None if candidate_ids is None else set(candidate_ids)
    for cand in result.candidates:
        if wanted is not None and cand.candidate_id not in wanted:
            continue
        sets: list[EvidenceScenarioSet] = []
        for vid in sorted(spec.evidence_requests):
            if vid == cand.variable_id or vid not in base or vid not in rows:
                continue  # the searched input is the unknown being solved for; it is not varied against itself
            s = build_scenario_set(rows[vid], canon[vid])
            if s is not None:
                sets.append(s)
        pr = next(p for p in spec.parameters if p.variable_id == cand.variable_id)
        pb = bounds.get(cand.variable_id)
        # X3 widens the search window; it must stay inside the model's declared physical bounds for this input
        domain = None if pb is None else (
            float("-inf") if pb.min is None else pb.min, float("inf") if pb.max is None else pb.max)

        def make(over, _v=cand.variable_id):  # type: ignore[no-untyped-def]
            return make_g(model, opts, metric, {**base, **over}, _v)

        ev = evaluate_candidate_evidence(
            candidate_id=cand.candidate_id,
            model_ref=result.model_ref,
            make_g=make,
            base=base,
            search_var=cand.variable_id,
            unit=cand.unit,
            root=_root_of(cand),
            lo=cand.search_low,
            hi=cand.search_high,
            grid_points=cand.grid_points,
            xtol=pr.xtol,
            scenario_sets=sets,
            config=cfg,
            falsification_config=falsification_config,
            perturb_vars=[k for k in sorted(base) if k != cand.variable_id],
            robustness_perturbations=[p for p in robustness_perturbations if p.name != cand.variable_id],
            robustness_config=robustness_config,
            reference_roots=reference_roots,
            domain=domain,
            expected_units=canon,
        )
        evaluations.append(ev)
        axes[cand.candidate_id] = scoring.score_axes(classes, ev.decision.classification)

    by_id = {c.candidate_id: c for c in result.candidates}
    ranking = [
        e.candidate_id
        for e in sorted(
            evaluations,
            key=lambda e: scoring.rank_key(
                by_id[e.candidate_id].score.total, e.decision.classification, e.candidate_id
            ),
        )
    ]
    return EvidenceDiscoveryOutcome(result, evaluations, ranking, axes)
