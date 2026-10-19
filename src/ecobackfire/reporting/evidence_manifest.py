"""Manifest + reproduction check for evidence-sensitive evaluations (layer 5; contracts + hashing only).

The manifest is a deterministic function of the evaluations: no clock, no host. ``reproduce`` re-runs a
caller-supplied evaluation function and compares the input hash exactly and the results structurally, with the
project's documented float tolerance (``reporting.manifest.TOLERANCE``) used only to describe numerically-equal
differences; a differing ``inputs_sha256`` means the evaluation was not re-run on the same inputs.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from ecobackfire.contracts.evidence_sensitivity import (
    RULE_DEFINITIONS,
    CandidateEvidenceEvaluation,
    DatasetUse,
    EvidenceSensitivityConfig,
    EvidenceSensitivityManifest,
)
from ecobackfire.core.hashing import canonical_json, canonicalize, sha256_text
from ecobackfire.reporting.manifest import TOLERANCE, json_close


def _inputs_payload(evals: Sequence[CandidateEvidenceEvaluation]) -> list[dict[str, object]]:
    return [
        {
            "candidate_id": e.candidate_id,
            "model_ref": e.model_ref,
            "variable_id": e.variable_id,
            "unit": e.unit,
            "root": e.root,
            "beneficial_side": e.beneficial_side,
            "search": [e.search_low, e.search_high],
            "config": e.config.model_dump(mode="json"),
            "scenario_sets": [s.model_dump(mode="json") for s in e.scenario_sets],
            "falsification_settings": e.falsification_settings,
            "robustness_settings": e.robustness_settings,
        }
        for e in sorted(evals, key=lambda e: e.candidate_id)
    ]


def _results_payload(evals: Sequence[CandidateEvidenceEvaluation]) -> list[dict[str, object]]:
    return [
        {
            "candidate_id": e.candidate_id,
            "outcomes": [o.model_dump(mode="json") for o in e.outcomes],
            "challenges": [c.model_dump(mode="json") for c in e.challenges],
            "dimensions": [d.model_dump(mode="json") for d in e.dimensions],
            "decision": e.decision.model_dump(mode="json"),
            "deterministic": e.deterministic,
            "falsification_report_sha256": e.falsification_report_sha256,
            "robustness_report_sha256": e.robustness_report_sha256,
        }
        for e in sorted(evals, key=lambda e: e.candidate_id)
    ]


def build_evidence_manifest(
    label: str, evals: Sequence[CandidateEvidenceEvaluation], config: EvidenceSensitivityConfig | None = None
) -> EvidenceSensitivityManifest:
    if not evals:
        raise ValueError("a manifest needs at least one evaluation")
    models = {e.model_ref for e in evals}
    if len(models) != 1:
        raise ValueError(f"one manifest covers one model; got {sorted(models)}")
    ids = [e.candidate_id for e in evals]
    if len(ids) != len(set(ids)):
        raise ValueError("candidate ids must be unique within a manifest")
    uses: dict[tuple[str, str], str | None] = {}
    keys: set[str] = set()
    for e in evals:
        for s in e.scenario_sets:
            keys.update(s.source_keys)
            for sc in s.scenarios:
                uses[(sc.dataset_id, sc.dataset_version)] = sc.release_content_sha256
    datasets = [DatasetUse(dataset_id=d, version=v, release_content_sha256=h) for (d, v), h in sorted(uses.items())]
    cfg = config or evals[0].config
    ordered = sorted(evals, key=lambda e: e.candidate_id)
    inputs = {"model_ref": next(iter(models)), "rules": RULE_DEFINITIONS, "evaluations": _inputs_payload(evals)}
    results = {"evaluations": _results_payload(evals)}
    return EvidenceSensitivityManifest(
        label=label,
        model_ref=next(iter(models)),
        datasets=datasets,
        source_keys=sorted(keys),
        classification_rules=dict(RULE_DEFINITIONS),
        config=cfg,
        evaluations=ordered,
        inputs_sha256=sha256_text(canonical_json(canonicalize(inputs))),
        results_sha256=sha256_text(canonical_json(canonicalize(results))),
    )


@dataclass
class EvidenceReproduceOutcome:
    ok: bool  # equivalent scientific results (within the documented tolerance)
    exact: bool  # byte-identical results hash
    details: list[str] = field(default_factory=list)


def reproduce_evidence_evaluation(
    manifest: EvidenceSensitivityManifest,
    rerun: Callable[[], Sequence[CandidateEvidenceEvaluation]],
) -> EvidenceReproduceOutcome:
    """Re-run and compare. ``rerun`` must evaluate the SAME inputs the manifest records."""
    new = build_evidence_manifest(manifest.label, list(rerun()), manifest.config)
    if new.inputs_sha256 != manifest.inputs_sha256:
        d = json_close(
            canonicalize(manifest.model_dump(mode="json")["evaluations"]),
            canonicalize(new.model_dump(mode="json")["evaluations"]),
            TOLERANCE["rtol"],
            TOLERANCE["atol"],
        )
        return EvidenceReproduceOutcome(
            False, False, ["the re-run used different inputs than the manifest records (inputs_sha256 differs)", *d[:5]]
        )
    if new.results_sha256 == manifest.results_sha256:
        return EvidenceReproduceOutcome(True, True, [])
    old_r = canonicalize({"evaluations": _results_payload(manifest.evaluations)})
    new_r = canonicalize({"evaluations": _results_payload(new.evaluations)})
    diffs = json_close(old_r, new_r, TOLERANCE["rtol"], TOLERANCE["atol"])
    if diffs:
        return EvidenceReproduceOutcome(False, False, [f"results differ beyond tolerance: {diffs[:5]}"])
    return EvidenceReproduceOutcome(True, False, ["results hash differs but all numbers agree within tolerance"])
