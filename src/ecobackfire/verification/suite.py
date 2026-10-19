"""Verification suite: runs all checks and applies the labeling gate."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ecobackfire.contracts.experiment import ExperimentConfiguration, ExperimentResult
from ecobackfire.contracts.verification import CheckResult, VerificationReport
from ecobackfire.core.engine import prepare_inputs
from ecobackfire.core.model import Model
from ecobackfire.errors import EcoBackfireError
from ecobackfire.models.registry import get_model
from ecobackfire.verification.checks import ALL_CHECKS, SUITE_VERSION, Ctx
from ecobackfire.verification.labeling import label_from_checks

FALSIFICATION_FAMILIES = {
    "falsification_perturbation",
    "falsification_counterexample",
    "model_form",
    "boundary",
    "parameter_uncertainty",
    "edge",
    "stability",
    "evidence_gate",
}


def build_claim(config: ExperimentConfiguration, result: ExperimentResult) -> str | None:
    be = result.breakeven
    if config.kind != "breakeven" or be is None or not be.roots:
        return None
    r = be.roots[0]
    side = {
        "below": "beneficial below and not beneficial above",
        "above": "not beneficial below and beneficial above",
        "unknown": "with an undetermined direction",
    }[r.beneficial_side]
    extra = f" (first of {len(be.roots)} break-even points)" if len(be.roots) > 1 else ""
    return (
        f"Under system boundary {result.system_boundary.id} ({result.system_boundary.description}), demand form "
        f"'{result.options['demand_form']}', and the stated inputs (weakest provenance: "
        f"{result.weakest_provenance.value}), the intervention's decision metric changes sign at "
        f"{be.variable_id} = {r.value:.9g} {be.unit}{extra}: {side}."
    )


def verify(
    config: ExperimentConfiguration,
    result: ExperimentResult,
    model: Model | None = None,
    benchmarks_dir: Path | None = None,
    *,
    verified_evidence: Mapping[str, str] | None = None,
) -> VerificationReport:
    """Verify ``result``. For a configuration with evidence requests, ``verified_evidence`` (from
    ``experiments.evidence.materialize_config``) is required so the evidence-backed bindings are accepted."""
    model = model or get_model(config.model_ref)
    opts = model.resolve_options(config.options)
    required = model.required_inputs(opts)
    verified = dict(verified_evidence or {})
    base = prepare_inputs(model.spec, config.bindings, required, verified_evidence=verified).values
    ctx = Ctx(
        config=config,
        result=result,
        model=model,
        opts=opts,
        base=base,
        required=required,
        benchmarks_dir=benchmarks_dir,
        verified_evidence=verified,
    )
    checks: list[CheckResult] = []
    for fn in ALL_CHECKS:
        try:
            checks.append(fn(ctx))
        except EcoBackfireError as exc:  # a check crashing is itself recorded, never swallowed
            checks.append(
                CheckResult(
                    check_id=fn.__name__,
                    family="check_error",
                    required=True,
                    status="inconclusive",
                    summary=f"check raised {type(exc).__name__}: {exc}",
                    details={},
                )
            )
    claim = build_claim(config, result)
    label, just = label_from_checks(checks, has_claim=claim is not None)

    unresolved = [
        "Prior-art review has not been performed (prior_art_status = not_reviewed): no novelty claim is made.",
        "Model-form adequacy against real data has not been assessed; constant-elasticity demand is an assumption.",
        "Indirect/economy-wide rebound, grid dynamics, maintenance and end-of-life emissions are excluded.",
    ]
    ev = result.evidence_resolution
    if ev is not None and ev.inputs_without_evidence:
        unresolved.append(
            f"Only {ev.n_evidence} of {len(ev.inputs)} inputs come from evidence records "
            f"({ev.evidence_backed_inputs or 'none'} pass the evidence-backed gate); "
            f"{ev.inputs_without_evidence} are assumed or illustrative. The result is conditional on them."
        )
    elif result.weakest_provenance.value == "illustrative":
        unresolved.append("Every input is an illustrative placeholder: no real-world evidence has been integrated.")
    for c in checks:
        if c.status in ("skipped", "inconclusive"):
            unresolved.append(f"{c.check_id}: {c.status}: {c.summary}")

    survived = [
        f"{c.check_id}: {c.summary}" for c in checks if c.family in FALSIFICATION_FAMILIES and c.status == "passed"
    ]
    refuted = [
        f"{c.check_id}: {c.summary}"
        for c in checks
        if c.family in FALSIFICATION_FAMILIES and c.status in ("failed", "limits_claim")
    ]
    profile = "breakeven_finding" if claim is not None else "pointwise"
    return VerificationReport(
        experiment_id=config.experiment_id,
        config_sha256=result.config_sha256,
        profile=profile,
        claim=claim,
        checks=checks,
        unresolved_uncertainties=unresolved,
        failed_attempts_to_falsify=survived,
        successful_falsifications=refuted,
        recommended_label=label,
        label_justification=just,
        check_suite_version=SUITE_VERSION,
    )
