"""Markdown report for evidence-sensitive evaluations (layer 5). Pure and deterministic: no clock, no paths.

Sections per candidate: Evidence basis, Evidence scenarios, Sensitivity, Falsification, Robustness,
Classification, Limitations. The report always separates

    "The model produces this result under these documented inputs"
from
    "The real world behaves this way."

and never states the second.
"""

from __future__ import annotations

from collections.abc import Sequence

from ecobackfire.contracts.evidence_sensitivity import (
    DIMENSION_QUESTIONS,
    RULE_DEFINITIONS,
    CandidateEvidenceEvaluation,
    EvidenceSensitivityManifest,
)
from ecobackfire.discovery.scoring import ROBUSTNESS_TIER

MODEL_STATEMENT = "The model produces this result under these documented inputs."
REAL_WORLD_STATEMENT = "This report does NOT establish that the real world behaves this way."


def _g(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.10g}"


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def _render_candidate(e: CandidateEvidenceEvaluation) -> list[str]:
    d = e.decision
    out = [
        "",
        f"## Candidate `{e.candidate_id}`",
        "",
        f"Model `{e.model_ref}`: break-even of `{e.variable_id}` at {_g(e.root)} {e.unit} "
        f"(beneficial side: {e.beneficial_side}), searched in [{_g(e.search_low)}, {_g(e.search_high)}] {e.unit}.",
        "",
        f"**Classification: `{d.classification}`** (numerical axis `{d.numerical_class}`, "
        f"evidence axis `{d.evidence_class}`; rule {d.rule_id}; robustness tier {ROBUSTNESS_TIER[d.classification]}).",
        "",
        MODEL_STATEMENT + " " + REAL_WORLD_STATEMENT,
    ]
    # --- evidence basis
    out += ["", "### Evidence basis", ""]
    if not e.scenario_sets:
        out.append("No input of this candidate has an evidence request. No documented source supplied any input.")
    else:
        rows = []
        for s in e.scenario_sets:
            for key in s.source_keys or ["-"]:
                recs = sorted(
                    {(x.dataset_id, x.dataset_version, x.evidence_id) for x in s.scenarios if x.source_key == key}
                )
                rows.append(
                    [
                        f"`{s.parameter}`",
                        f"`{key}`",
                        "; ".join(f"`{ds}@{v}`" for ds, v, _ in recs) or "-",
                        s.resolution_state,
                        s.resolution_decision,
                        f"`{s.baseline_evidence_id}`" if s.baseline_evidence_id else "none (no record was chosen)",
                    ]
                )
        out += _table(["input", "source", "dataset@version", "resolution", "decision", "baseline record"], rows)
        for s in e.scenario_sets:
            for r in s.excluded:
                out.append(f"- `{s.parameter}` record not evaluated: {r}")
    # --- scenarios
    out += ["", "### Evidence scenarios", ""]
    rows = [
        [
            f"`{sc.scenario_id}`",
            sc.role,
            f"{_g(sc.value)} {sc.unit}",
            f"{_g(sc.original_value)} {sc.original_unit or ''}".strip(),
            sc.provenance_class,
            sc.geography or "-",
            sc.period or "-",
            "yes" if sc.is_baseline else "",
        ]
        for s in e.scenario_sets
        for sc in s.scenarios
    ]
    if rows:
        heads = ["scenario", "role", "value (canonical)", "as documented", "provenance", "geography", "period", "base"]
        out += _table(heads, rows)
        for s in e.scenario_sets:
            for r in s.ranges:
                out.append(
                    f"- `{s.parameter}` documented range from `{r.evidence_id}`: "
                    f"[{_g(r.lower)}, {_g(r.upper)}] {r.unit}"
                    + (f", central {_g(r.central)}" if r.central is not None else "")
                    + ". An interval stated by the source; no distribution is implied."
                )
    else:
        out.append("No documented range or alternative source exists for any input, so no scenario was evaluated.")
    # --- sensitivity
    out += ["", "### Sensitivity", ""]
    if e.outcomes:
        rows = [
            [f"`{o.scenario_id}`", o.challenge_type, f"{_g(o.value)}", o.outcome, _g(o.root), o.reason or "-"]
            for o in e.outcomes
        ]
        out += _table(["scenario", "challenge", "value", "model result", "break-even", "note"], rows)
        kept = [o.scenario_id for o in e.outcomes if o.outcome == "preserved"]
        lost = [o.scenario_id for o in e.outcomes if o.lost]
        out += ["", f"Preserved the finding: {len(kept)}. Eliminated or reversed it: {len(lost)} {lost or ''}".rstrip()]
    else:
        out.append("No scenarios were evaluated.")
    # --- falsification
    out += ["", "### Falsification", ""]
    out += _table(
        ["challenge type", "check", "status", "result"],
        [[c.challenge_type, f"`{c.check_id}`", c.status, c.summary] for c in e.challenges],
    )
    out += [
        "",
        "Failed challenge type(s): " + (", ".join(d.failed_challenge_types) or "none") + ". "
        "Limiting (survived only the smaller challenges): " + (", ".join(d.limiting_challenge_types) or "none") + ".",
    ]
    # --- robustness
    out += ["", "### Robustness", ""]
    out += _table(
        ["dimension", "status", "cases", "preserved", "lost", "unresolved", "basis"],
        [[x.dimension, x.status, str(x.n_cases), str(x.n_preserved), str(x.n_lost), str(x.n_unresolved), x.basis]
         for x in e.dimensions],
    )
    out += [""] + [f"- **{x.dimension}**: {DIMENSION_QUESTIONS[x.dimension]}" for x in e.dimensions]
    out += ["", "The dimensions are reported separately and are not combined into one score. Evidence robustness is "
            "not confidence and not a probability."]
    # --- classification
    out += ["", "### Classification", ""]
    out += [f"- {r}" for r in d.reasons]
    out += [f"- rule {d.rule_id}: {RULE_DEFINITIONS[d.rule_id]}"]
    if d.not_assessed:
        out.append("- not assessed: " + ", ".join(d.not_assessed))
    # --- limitations
    out += ["", "### Limitations", ""] + [f"- {x}" for x in e.limitations]
    out += ["", f"Reproducibility: repeated evaluation deterministic = {e.deterministic}; falsification report "
            f"sha256 `{e.falsification_report_sha256}`" + (f"; robustness report sha256 `{e.robustness_report_sha256}`"
                                                              if e.robustness_report_sha256 else "") + "."]
    return out


def render_evidence_sensitivity_report(
    title: str, evals: Sequence[CandidateEvidenceEvaluation], manifest: EvidenceSensitivityManifest | None = None
) -> str:
    out = [f"# {title}", "", f"> {MODEL_STATEMENT} {REAL_WORLD_STATEMENT}", ""]
    out += ["Classification terms used here are defined by the rules listed in the manifest "
            "(`classification_rules`); they are categorical and contain no probability."]
    if manifest is not None:
        out += ["", f"Inputs sha256 `{manifest.inputs_sha256}`; results sha256 `{manifest.results_sha256}`; "
                f"datasets: {', '.join(f'`{d.dataset_id}@{d.version}`' for d in manifest.datasets) or 'none'}."]
    for e in sorted(evals, key=lambda x: x.candidate_id):
        out += _render_candidate(e)
    return "\n".join(out) + "\n"
