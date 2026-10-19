"""Pure function: (config, result, verification) -> Markdown. No timestamps, no clock, no network."""

from __future__ import annotations

from ecobackfire import __version__
from ecobackfire.contracts.common import ILLUSTRATIVE_MARKER, IntervalUncertainty
from ecobackfire.contracts.experiment import ExperimentConfiguration, ExperimentResult
from ecobackfire.contracts.resolution import EvidenceResolutionSummary
from ecobackfire.contracts.verification import VerificationReport
from ecobackfire.models.efficiency_rebound.model import BOUNDARY_VARIANTS
from ecobackfire.reporting.formatting import format_kv
from ecobackfire.verification.labeling import LABEL_DEFINITIONS


def _g(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.10g}"


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def render_evidence_section(ev: EvidenceResolutionSummary) -> list[str]:
    """Where every input came from. Pure; shared by experiment and discovery reports."""
    out: list[str] = ["", "## Evidence resolution", ""]
    out.append(
        f"{ev.n_evidence} input(s) from dataset records, {ev.n_assumed} assumed, {ev.n_illustrative} illustrative. "
        f"Inputs passing the evidence-backed gate (reviewed, scoped, uncertainty characterized): "
        f"{ev.evidence_backed_inputs or 'none'}. Dataset registry sha256 `{ev.registry_sha256}`."
    )
    out.append("")
    rows = []
    for r in ev.inputs:
        s = r.selected
        if s is None:
            rows.append([f"`{r.variable_id}`", r.basis, "-", "-", "-", "-", "-", "-"])
            continue
        conv = (
            f"{_g(s.conversion.original_value)} {s.conversion.original_unit} -> "
            f"{_g(s.conversion.canonical_value)} {s.conversion.canonical_unit}"
            if s.conversion.performed
            else "none"
        )
        rows.append(
            [
                f"`{r.variable_id}`",
                r.basis,
                f"`{s.evidence_id}`",
                s.status,
                f"{_g(s.value)} {s.unit}",
                conv,
                f"{s.geography_text or '-'} / {s.period_text or '-'} ({s.geography_match} match)",
                (s.source.source_id if s.source else "-"),
            ]
        )
    headers = ["input", "basis", "evidence id", "status", "value (as recorded)", "unit conversion", "scope", "source"]
    out += _table(headers, rows)
    for r in ev.inputs:
        if r.selected is None:
            continue
        out.append("")
        out.append(
            f"**`{r.variable_id}`** selected by rule `{r.selection_rule}`; {len(r.applicable)} applicable "
            f"record(s), {len(r.excluded)} excluded."
        )
        for w in r.warnings:
            out.append(f"- {w}")
        for ex in r.excluded:
            out.append(f"- excluded `{ex.evidence_id}`: " + "; ".join(f"{x.code}" for x in ex.reasons))
    return out


def render_report(
    config: ExperimentConfiguration,
    result: ExperimentResult,
    verification: VerificationReport | None,
    reproduce_command: str,
) -> str:
    from ecobackfire.models.registry import get_model  # local import keeps module import light

    model = get_model(config.model_ref)
    L: list[str] = []
    a = L.append
    a(f"# {config.title}")
    a("")
    a(f"- Experiment: `{result.experiment_id}`  ·  model: `{result.model_ref}`  ·  kind: `{result.kind}`")
    a(f"- Config hash (sha256): `{result.config_sha256}`")
    a(f"- Package version: `{__version__}`")
    a("")
    if result.weakest_provenance.value == "illustrative":
        a(
            f"> **{ILLUSTRATIVE_MARKER}.** This result contains illustrative inputs. It demonstrates the "
            "method, not real-world conclusions, and says nothing about any real product, policy, or technology."
        )
        a("")
    a("## Research question")
    a("")
    a(config.research_question.strip())
    a("")
    a("## Model, functional unit, and system boundary")
    a("")
    a(f"- **Model:** {model.spec.title} (`{model.spec.ref}`, status `{model.spec.status}`)")
    a(
        f"- **Boundary variant:** `{result.options['boundary_variant']}`: "
        f"{BOUNDARY_VARIANTS[result.options['boundary_variant']]}"
    )
    a(f"- **Demand form:** `{result.options['demand_form']}`")
    a(
        f"- **Functional unit:** {result.functional_unit.description}. "
        f"{result.functional_unit.reference_flow_description}"
    )
    a(
        f"- **System boundary `{result.system_boundary.id}` includes:** "
        f"{'; '.join(result.system_boundary.included_stages)}"
    )
    a(f"- **Excluded:** {'; '.join(result.system_boundary.excluded_stages)}")
    a(f"- **Decision rule (`{config.decision_metric_ref}`):** {model.spec.decision_metrics[0].direction_text}")
    a("")
    a("**Declared assumptions**")
    a("")
    for asm in model.spec.assumptions.assumptions:
        a(f"- {asm.id}: {asm.statement}")
    a("")
    a("**Declared exclusions:** " + "; ".join(model.spec.assumptions.exclusions))
    a("")
    a("## Inputs and provenance")
    a("")
    rows = []
    for vid, b in sorted(config.bindings.items()):
        unc = (
            f"interval [{_g(b.uncertainty.low)}, {_g(b.uncertainty.high)}]"
            if isinstance(b.uncertainty, IntervalUncertainty)
            else b.uncertainty.kind
        )
        rows.append([f"`{vid}`", _g(b.value), b.unit, b.provenance_class.value, unc])
    L += _table(["input", "value", "unit", "provenance", "uncertainty"], rows)
    a("")
    a(
        f"Weakest provenance class among inputs: **{result.weakest_provenance.value}**. "
        "(Influence-weighted provenance is not yet implemented; the conservative all-inputs rule is used.)"
    )
    if result.evidence_resolution is not None:
        L += render_evidence_section(result.evidence_resolution)
    if result.warnings:
        a("")
        a("**Warnings recorded by the engine:**")
        for w in result.warnings:
            a(f"- {w}")
    a("")
    a("## Results")
    a("")
    if result.compare:
        rows = []
        for s in result.compare:
            o = s.outputs
            rows.append(
                [
                    s.name,
                    format_kv(s.overrides, lambda v: _g(v)) or "(none)",  # type: ignore[arg-type]
                    _g(o["E1"].value),
                    _g(o["dC"].value),
                    s.decision.label,
                    _g(o["rebound_R"].value) if "rebound_R" in o else "n/a",
                    _g(o["intensity_ratio"].value) if "intensity_ratio" in o else "n/a",
                ]
            )
        L += _table(
            [
                "scenario",
                "overrides",
                "E1 (kWh/yr)",
                "dC (kgCO2e/yr)",
                "decision (absolute emissions)",
                "rebound R",
                "intensity ratio (per unit service)",
            ],
            rows,
        )
        a("")
        div = [
            s.name
            for s in result.compare
            if s.decision.label == "not_beneficial"
            and "intensity_ratio" in s.outputs
            and s.outputs["intensity_ratio"].value < 1.0
        ]
        if div:
            a(
                f"**Functional-unit divergence:** in scenario(s) {', '.join(div)} absolute annual emissions RISE while "
                "emissions per unit of service FALL. Which functional unit is the right one is a decision framing, not "
                "a modelling result. Higher service is also a welfare gain; this tool does not weigh welfare."
            )
            a("")
    if result.breakeven:
        be = result.breakeven
        a(
            f"**Break-even search** over `{be.variable_id}` ∈ [{_g(be.search_low)}, {_g(be.search_high)}] {be.unit} "
            f"({be.grid_points} grid points, xtol {be.xtol:g}): classification `{be.classification}`."
        )
        a("")
        a(be.note)
        a("")
        if be.roots:
            rows = [
                [
                    _g(r.value),
                    r.kind,
                    f"[{_g(r.bracket_low)}, {_g(r.bracket_high)}]",
                    f"{r.residual:.3g}",
                    str(r.iterations),
                    r.beneficial_side,
                ]
                for r in be.roots
            ]
            L += _table(
                [f"{be.variable_id} at break-even", "kind", "bracket", "residual g", "iterations", "beneficial side"],
                rows,
            )
            a("")
            if be.trace_at_first_root:
                a("Calculation trace at the first break-even point:")
                a("")
                for st in be.trace_at_first_root:
                    a(
                        f"- {st['equation']}: {st['formula']}  →  "
                        + format_kv(dict(st["output"]), lambda v: _g(float(v)))  # type: ignore[arg-type]
                    )  # type: ignore[arg-type]
                a("")
    if result.sensitivity:
        s = result.sensitivity
        a(
            f"**Sensitivity ({s.method})**, target `{s.target}` ({s.target_unit}); base value {_g(s.base_target)}. "
            "Ranges come from the sources listed (binding intervals here are illustrative placeholders)."
        )
        a("")
        rows = [
            [
                f"`{r.variable_id}`",
                r.range_source,
                f"{_g(r.low_value)} … {_g(r.high_value)} {r.unit}",
                _g(r.target_at_low),
                _g(r.target_at_high),
                _g(r.swing),
            ]
            for r in s.rows
        ]
        L += _table(["input", "range source", "range", "target at low", "target at high", "swing"], rows)
        if s.skipped:
            a("")
            a("Skipped: " + "; ".join(s.skipped))
        a("")
        a("OAT shows one-at-a-time influence only; it cannot reveal interactions between inputs.")
        a("")

    if verification is not None:
        v = verification
        a("## Verification and falsification")
        a("")
        a(f"**Recommended label: `{v.recommended_label}`.** {LABEL_DEFINITIONS[v.recommended_label]}")
        a("")
        if v.claim:
            a(f"**Claim under test:** {v.claim}")
            a("")
        L += _table(
            ["check", "family", "status", "summary"],
            [[f"`{c.check_id}`", c.family, f"**{c.status}**", c.summary] for c in v.checks],
        )
        a("")
        a("**Label justification:**")
        for j in v.label_justification:
            a(f"- {j}")
        a("")
        a("**Falsification attempts that did not refute the claim:**")
        for x in v.failed_attempts_to_falsify or ["(none)"]:
            a(f"- {x}")
        a("")
        a("**Falsification attempts that narrowed or refuted the claim:**")
        for x in v.successful_falsifications or ["(none)"]:
            a(f"- {x}")
        a("")
        a("**Unresolved uncertainties:**")
        for x in v.unresolved_uncertainties:
            a(f"- {x}")
        a("")
    a("## Limitations")
    a("")
    for lim in [
        "Direct rebound only; indirect and economy-wide rebound are excluded.",
        "Constant-elasticity (or saturating) demand is assumed; neither form has been checked against real data here.",
        "Static annual model: no change in prices or emissions factors over the amortization period.",
        "Embodied emissions are linearly amortized; no maintenance, replacement, or end-of-life emissions.",
        "Single impact category (greenhouse gases).",
        "Break-even detection is grid-based: even-multiplicity roots and sign changes narrower than the grid "
        "spacing cannot be excluded (dense re-sampling is used as a falsification attempt, not a proof).",
        "Software tests and model verification show the implementation solves the stated equations; they do "
        "not show that the equations describe any real system.",
        "No prior-art review has been done; no claim of novelty is made.",
    ]:
        a(f"- {lim}")
    a("")
    a("## Reproduction")
    a("")
    a("```")
    a(reproduce_command)
    a("```")
    a("")
    a(
        "`manifest.json` in the run directory records the code version, dependency versions, config hash, and "
        "artifact hashes; `ecobackfire reproduce <run_dir>/manifest.json` re-runs and compares them."
    )
    a("")
    return "\n".join(L)
