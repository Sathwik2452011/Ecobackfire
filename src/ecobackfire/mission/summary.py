"""Mission disclosure helpers (layer 7): what evidence existed, how hard a candidate was challenged, what was
verified, what it cost.

Everything here is PURE and imports nothing from ecobackfire (stdlib only, duck-typed inputs, plain-dict outputs);
``mission.runner`` wraps the dicts in the contract models. Nothing here changes any classification, score or
search: it only counts, copies and describes what the existing stages already produced.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

# Evidence classes (existing ``EvidenceClass`` vocabulary) grouped for the summary. The grouping is a counting
# convention, documented in ``SUMMARY_DEFINITIONS``; each class is also reported on its own in ``by_evidence_class``.
SOURCE_DEPENDENT_CLASSES: tuple[str, ...] = (
    "single_source_dependent",
    "evidence_boundary_dependent",
    "evidence_sensitive",
)
ALL_EVIDENCE_CLASSES: tuple[str, ...] = (
    "evidence_robust",
    "eliminated_by_evidence",
    "single_source_dependent",
    "evidence_boundary_dependent",
    "evidence_sensitive",
    "evidence_insufficient",
    "evidence_inconclusive",
)

SUMMARY_DEFINITIONS: dict[str, str] = {
    "total_candidates": "candidates produced by discovery (before top_k selection)",
    "candidates_evaluated": "candidates that went through falsification, robustness and evidence challenges",
    "candidates_with_evidence_variation": "evaluated candidates for which at least one documented evidence scenario "
    "OTHER than the candidate's own base value exists (a documented range or an alternative source)",
    "evidence_insufficient": "evaluated candidates with evidence_class 'evidence_insufficient' "
    "(no documented variation)",
    "source_dependent": "evaluated candidates whose evidence_class is single_source_dependent, "
    "evidence_boundary_dependent or evidence_sensitive: the modeled finding depends on which documented value is used",
    "eliminated_by_evidence": "evaluated candidates that no documented evidence scenario preserved",
    "evidence_robust": "evaluated candidates preserved in every documented evidence scenario",
    "evidence_inconclusive": "evaluated candidates with unresolved (invalid / near-edge) evidence scenarios",
}


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


# ------------------------------------------------------------------ evidence summary


def evidence_summary(
    total_candidates: int,
    evaluations: Sequence[Any],
    evidence_items: Sequence[Any] = (),
    assumed_inputs: Sequence[str] = (),
) -> dict[str, Any]:
    """Count the evidence state of a finished investigation.

    ``evaluations``: ``CandidateEvidenceEvaluation``-like objects (``candidate_id``, ``decision.evidence_class``,
    ``outcomes[*].is_baseline``, ``scenario_sets[*]``). ``evidence_items``: plan ``EvidenceItem``-like objects.
    """
    by_class = {k: 0 for k in ALL_EVIDENCE_CLASSES}
    with_variation = 0
    for e in evaluations:
        cls = str(_get(_get(e, "decision"), "evidence_class"))
        by_class[cls] = by_class.get(cls, 0) + 1
        if any(not _get(o, "is_baseline", False) for o in _get(e, "outcomes", [])):
            with_variation += 1
    n_eval = len(evaluations)
    source_dep = sum(by_class.get(k, 0) for k in SOURCE_DEPENDENT_CLASSES)
    insufficient = by_class["evidence_insufficient"]

    # per-input availability: the plan's resolution state, plus what scenario sets actually held
    sets: dict[str, dict[str, int]] = {}
    for e in evaluations:
        for s in _get(e, "scenario_sets", []):
            p = str(_get(s, "parameter"))
            cur = sets.setdefault(p, {"n_sources": 0, "n_documented_ranges": 0, "n_scenarios": 0})
            n_src = _get(s, "n_sources", None)
            if n_src is None:
                n_src = len(_get(s, "source_keys", []))
            cur["n_sources"] = max(cur["n_sources"], int(n_src))
            cur["n_documented_ranges"] = max(cur["n_documented_ranges"], len(_get(s, "ranges", [])))
            cur["n_scenarios"] = max(cur["n_scenarios"], len(_get(s, "scenarios", [])))
    available: list[dict[str, Any]] = []
    for it in sorted(evidence_items, key=lambda i: str(_get(i, "variable_id"))):
        vid = str(_get(it, "variable_id"))
        info = sets.get(vid, {})
        alts = _get(it, "alternatives", {}) or {}
        available.append(
            {
                "variable_id": vid,
                "state": str(_get(it, "state")),
                "evidence_id": _get(it, "evidence_id"),
                "status": _get(it, "status"),
                "n_sources": info.get("n_sources"),
                "n_documented_ranges": info.get("n_documented_ranges"),
                "n_alternative_records": len(alts),
                "n_scenarios": info.get("n_scenarios"),
                "has_documented_variation": bool(
                    info.get("n_documented_ranges", 0) > 0 or info.get("n_sources", 0) > 1
                ),
            }
        )

    stmt = (
        f"{with_variation} of {n_eval} evaluated candidate(s) have documented evidence variation "
        f"(a documented range or an alternative source); {insufficient} evidence_insufficient, "
        f"{source_dep} source_dependent, {by_class['evidence_robust']} evidence_robust, "
        f"{by_class['eliminated_by_evidence']} eliminated_by_evidence, "
        f"{by_class['evidence_inconclusive']} evidence_inconclusive."
    )
    if total_candidates > n_eval:
        stmt += f" {total_candidates - n_eval} of {total_candidates} candidate(s) were not evaluated (outside top_k)."
    if n_eval and with_variation == 0:
        stmt += (
            " No candidate was challenged by documented evidence variation: the status 'completed' means the pipeline "
            "ran, not that the candidates are evidence-supported."
        )
    notes: list[str] = []
    if with_variation != n_eval - insufficient:
        notes.append(
            "count mismatch: candidates_with_evidence_variation != candidates_evaluated - evidence_insufficient; "
            "inspect the per-candidate outcomes"
        )
    return {
        "total_candidates": total_candidates,
        "candidates_evaluated": n_eval,
        "candidates_not_evaluated": max(0, total_candidates - n_eval),
        "candidates_with_evidence_variation": with_variation,
        "evidence_insufficient": insufficient,
        "source_dependent": source_dep,
        "eliminated_by_evidence": by_class["eliminated_by_evidence"],
        "evidence_robust": by_class["evidence_robust"],
        "evidence_inconclusive": by_class["evidence_inconclusive"],
        "by_evidence_class": by_class,
        "evidence_available": available,
        "inputs_without_registry_evidence": sorted(assumed_inputs),
        "statement": stmt,
        "definitions": dict(SUMMARY_DEFINITIONS),
        "notes": notes,
    }


# ------------------------------------------------------------------ settings disclosure and audit


def effective_perturbations(specs: Sequence[Any]) -> list[dict[str, Any]]:
    """Plain description of the robustness perturbations that were actually applied (``PerturbationSpec``-like)."""
    out: list[dict[str, Any]] = []
    for p in sorted(specs, key=lambda s: str(_get(s, "name"))):
        base, lo, hi = float(_get(p, "baseline")), float(_get(p, "low")), float(_get(p, "high"))
        half = (hi - lo) / 2.0 / abs(base) if base else None
        out.append(
            {
                "variable_id": str(_get(p, "name")),
                "unit": str(_get(p, "unit")),
                "baseline": base,
                "low": lo,
                "high": hi,
                "relative_half_width": half,
                "relative_half_width_percent": None if half is None else half * 100.0,
                "n_evals": int(_get(p, "n_evals")),
                "basis": str(_get(p, "basis")),
                "mode": "one_at_a_time",
            }
        )
    return out


def settings_disclosure(
    falsification: Mapping[str, Any],
    robustness: Mapping[str, Any],
    applied_perturbations: Sequence[Mapping[str, Any]],
    skipped_notes: Sequence[str],
    evidence_challenge: Mapping[str, Any],
    applied: bool,
) -> dict[str, Any]:
    """The settings actually used (or, when ``applied`` is False, only declared), with an explicit scope statement."""
    pert_ids = [p["variable_id"] for p in applied_perturbations]
    lv = list(falsification.get("perturbation_levels", []))
    lv_txt = ", ".join(f"+/-{x * 100:g}%" for x in lv) if lv else "none"
    pert_txt = (
        "; ".join(f"{p['variable_id']}: +/-{p['relative_half_width_percent']:g}% ({p['n_evals']} evaluations)"
                  for p in applied_perturbations if p["relative_half_width_percent"] is not None)
        or "none"
    )
    scope = (
        "This is NOT a broad robustness analysis. Every perturbation is applied to ONE input at a time with all "
        "other inputs held at base values, so interactions between inputs are not tested. "
        f"Falsification X5 perturbs each other input individually by {lv_txt}; "
        f"the mission's robustness analysis (R1) perturbs only [{pert_txt}]. "
        "Perturbation ranges are configured sensitivity experiments, not uncertainty distributions."
    )
    return {
        "applied": applied,
        "perturbation_scheme": "one_at_a_time",
        "falsification": dict(falsification),
        "robustness": dict(robustness),
        "robustness_perturbations_applied": [dict(p) for p in applied_perturbations],
        "robustness_perturbed_parameters": pert_ids,
        "robustness_perturbations_skipped": list(skipped_notes),
        "evidence_challenge": dict(evidence_challenge),
        "scope_statement": scope,
    }


def audit_settings(
    falsification: Mapping[str, Any],
    robustness: Mapping[str, Any],
    applied_perturbations: Sequence[Mapping[str, Any]],
    skipped_notes: Sequence[str],
    default_falsification: Mapping[str, Any],
    default_robustness: Mapping[str, Any],
) -> dict[str, Any]:
    """Flag settings that are weaker than the project's OWN defaults, or that leave nothing to challenge with.

    No threshold here is invented: every comparison is against a project default (``FalsificationSettings`` /
    ``RobustnessSettings``) or against the number 2 (the minimum that allows a repeat comparison; the existing
    config validators already enforce it). Findings are warnings; nothing is rejected or changed.
    """
    f: list[dict[str, Any]] = []

    def add(code: str, severity: str, message: str, observed: Any = None, reference: Any = None) -> None:
        f.append({"code": code, "severity": severity, "message": message, "observed": observed, "reference": reference})

    for name, cfg, label in (
        ("falsification", falsification, "falsification"),
        ("robustness", robustness, "robustness"),
    ):
        if int(cfg.get("repeats", 2)) < 2:
            add(f"{name}_repeats_below_2", "warning",
                f"{label}.repeats < 2: a determinism comparison needs at least two executions", cfg.get("repeats"), 2)
    d = default_falsification
    lv, dlv = list(falsification.get("perturbation_levels", [])), list(d.get("perturbation_levels", []))
    if not lv:
        add(
            "falsification_no_perturbation_levels",
            "warning",
            "no falsification perturbation level is configured",
            lv,
            dlv,
        )
    else:
        if max(lv) < max(dlv):
            add("falsification_weaker_max_perturbation", "warning",
                f"largest falsification perturbation level {max(lv):g} is smaller than "
                f"the project default {max(dlv):g}",
                max(lv), max(dlv))
        if len(lv) < len(dlv):
            add("falsification_fewer_levels", "warning",
                f"{len(lv)} perturbation level(s) configured; the project default uses {len(dlv)}", lv, dlv)
    for key in ("agree_tol", "noise_tol"):
        if key in falsification and key in d and float(falsification[key]) > float(d[key]):
            add(f"falsification_{key}_more_permissive", "warning",
                f"falsification.{key}={falsification[key]:g} is more permissive than the project default {d[key]:g}",
                falsification[key], d[key])
    for key in ("edge_margin", "range_expansion"):
        if key in falsification and key in d and float(falsification[key]) < float(d[key]):
            add(f"falsification_{key}_weaker", "warning",
                f"falsification.{key}={falsification[key]:g} is smaller (weaker test) "
                f"than the project default {d[key]:g}",
                falsification[key], d[key])
    rd = default_robustness
    if float(robustness.get("agree_tol", 0)) > float(rd.get("agree_tol", 0)):
        add("robustness_agree_tol_more_permissive", "warning",
            f"robustness.agree_tol={robustness['agree_tol']:g} is more permissive than the project default "
            f"{rd['agree_tol']:g}", robustness["agree_tol"], rd["agree_tol"])
    if float(robustness.get("edge_margin", 0)) < float(rd.get("edge_margin", 0)):
        add("robustness_edge_margin_weaker", "warning",
            f"robustness.edge_margin={robustness['edge_margin']:g} is smaller than "
            f"the project default {rd['edge_margin']:g}",
            robustness["edge_margin"], rd["edge_margin"])
    if not applied_perturbations:
        add("robustness_no_perturbation_applied", "warning",
            "no mission-configured robustness perturbation was applied; parameter robustness rests on the "
            "falsification X5 perturbations alone")
    narrowest_default = min(dlv) if dlv else None
    for p in applied_perturbations:
        hw = p.get("relative_half_width")
        if hw is not None and narrowest_default is not None and hw < narrowest_default:
            add("robustness_perturbation_narrower_than_default_level", "warning",
                f"{p['variable_id']}: +/-{hw * 100:g}% is narrower than the narrowest default falsification "
                f"perturbation level ({narrowest_default * 100:g}%)", hw, narrowest_default)
    for n in skipped_notes:
        add("robustness_perturbation_skipped", "warning", n)
    add("one_at_a_time_only", "info",
        "all perturbations are one input at a time; multi-parameter interactions are not tested")
    n_warn = sum(1 for x in f if x["severity"] == "warning")
    return {
        "status": "warnings" if n_warn else "no_weakening_vs_project_defaults",
        "n_warnings": n_warn,
        "findings": f,
        "reference": "project defaults (FalsificationSettings / RobustnessSettings) and the existing config validators",
        "note": "The audit compares the mission's settings with the project's own defaults. It does not establish that "
        "the defaults are scientifically sufficient, and passing it does not mean a candidate is robust.",
    }


# ------------------------------------------------------------------ verification disclosure


def verification_disclosure(
    ranked: Sequence[Mapping[str, Any]], verified_candidate_id: str | None, label: str | None, performed: bool
) -> dict[str, Any]:
    """``ranked``: dicts with ``candidate_id`` and ``evaluated`` (bool), in rank order."""
    verified = [verified_candidate_id] if (performed and verified_candidate_id) else []
    falsified_only = [r["candidate_id"] for r in ranked if r["evaluated"] and r["candidate_id"] not in verified]
    discovery_only = [r["candidate_id"] for r in ranked if not r["evaluated"]]
    if verified:
        stmt = (
            f"Standard verification (the 12-check suite, run on the standard break-even experiment of the "
            f"top-ranked candidate '{verified[0]}', label: {label}) was applied to that candidate only. "
        )
    else:
        stmt = "The standard 12-check verification suite was not run for any candidate in this execution. "
    stmt += (
        f"{len(falsified_only)} other candidate(s) {falsified_only} received falsification and robustness analysis "
        "but were not independently verified with the full standard verification suite."
        if falsified_only
        else "No other candidate was falsified or verified."
    )
    if discovery_only:
        stmt += (
            f" {len(discovery_only)} candidate(s) {discovery_only} were reported from discovery only (not evaluated)."
        )
    return {
        "suite": "standard 12-check verification suite",
        "performed": bool(verified),
        "verified_candidate_ids": verified,
        "verification_label": label if verified else None,
        "falsified_and_robustness_tested_not_verified": falsified_only,
        "discovery_only_candidate_ids": discovery_only,
        "statement": stmt,
    }


# ------------------------------------------------------------------ budget usage


def budget_usage(
    max_model_evaluations: int,
    estimated_upper_bound: int | None,
    actual: int | None,
    actual_rerun: int | None,
    scenarios_used: Sequence[int],
    max_scenarios: int,
    n_candidates_evaluated: int,
    max_candidates: int,
) -> dict[str, Any]:
    reached = None if actual is None else actual >= max_model_evaluations
    remaining = None if actual is None else max(0, max_model_evaluations - actual)
    frac = None if actual is None else actual / max_model_evaluations
    stmt = (
        "model evaluations were not counted for this run"
        if actual is None
        else f"{actual} of {max_model_evaluations} allowed model evaluations were used ({frac * 100:.2f}%); "
        f"budget reached: {'yes' if reached else 'no'}; remaining: {remaining}"
    )
    return {
        "configured_max_model_evaluations": max_model_evaluations,
        "estimated_upper_bound_at_plan_time": estimated_upper_bound,
        "actual_model_evaluations": actual,
        "actual_model_evaluations_reproducibility_rerun": actual_rerun,
        "budget_reached": reached,
        "remaining_model_evaluations": remaining,
        "fraction_used": frac,
        "max_scenarios_per_candidate": max_scenarios,
        "max_scenarios_used_by_any_candidate": max(scenarios_used) if scenarios_used else 0,
        "candidates_evaluated": n_candidates_evaluated,
        "max_candidates": max_candidates,
        "statement": stmt,
        "counting_scope": (
            "actual = number of Model.evaluate calls made by the discovery, falsification, robustness and "
            "evidence-challenge pipeline in its first execution. It excludes the optional reproducibility re-run "
            "(reported separately), the standard experiment run and the 12-check verification suite, and the "
            "independent reference calculations."
        ),
        "estimate_note": (
            "The plan-time figure is a deliberately generous UPPER-BOUND ESTIMATE used only to refuse over-budget "
            "missions before they run; it is not a measurement and is not claimed to be accurate."
        ),
    }


@contextmanager
def count_model_evaluations(model: Any) -> Iterator[Callable[[], int]]:
    """Count calls to ``type(model).evaluate`` while active (class-level wrap, restored on exit). Yields a getter."""
    cls = type(model)
    orig = cls.evaluate
    n = [0]

    def counting(self: Any, *a: Any, **k: Any) -> Any:
        n[0] += 1
        return orig(self, *a, **k)

    cls.evaluate = counting  # type: ignore[method-assign]
    try:
        yield lambda: n[0]
    finally:
        cls.evaluate = orig  # type: ignore[method-assign]
