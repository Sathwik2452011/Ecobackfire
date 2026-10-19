"""Compact human-readable mission report. Renders an existing ``MissionResult``; computes nothing scientific."""

from __future__ import annotations

from ecobackfire.contracts.mission import MissionResult


def render_mission_report(r: MissionResult) -> str:
    L: list[str] = [
        f"# Mission report: {r.mission.mission_id}",
        "",
        f"status: **{r.status}**" + (f" (reason: {r.reason})" if r.reason else ""),
    ]
    if r.failure:
        L.append(f"failure at stage '{r.failure.stage}': {r.failure.message}")
    L += ["", "## Evidence summary"]
    es = r.evidence_summary
    if es is None:
        L.append("not available (the investigation did not reach discovery)")
    else:
        L += [
            es.statement,
            "",
            f"- total_candidates: {es.total_candidates}",
            f"- candidates_evaluated: {es.candidates_evaluated}",
            f"- candidates_with_evidence_variation: {es.candidates_with_evidence_variation}",
            f"- evidence_insufficient: {es.evidence_insufficient}",
            f"- source_dependent: {es.source_dependent}",
            f"- eliminated_by_evidence: {es.eliminated_by_evidence}",
            f"- evidence_robust: {es.evidence_robust}",
            f"- evidence_inconclusive: {es.evidence_inconclusive}",
            "",
            "Evidence available per requested input:",
        ]
        for a in es.evidence_available:
            L.append(
                f"- {a.variable_id}: {a.state}; record {a.evidence_id}; status {a.status}; sources {a.n_sources}; "
                f"documented ranges {a.n_documented_ranges}; alternative records {a.n_alternative_records}; "
                f"documented variation: {'yes' if a.has_documented_variation else 'no'}"
            )
        L.append(
            "Inputs without registry evidence (illustrative or assumed): "
            + (", ".join(es.inputs_without_registry_evidence) or "none")
        )
        for n in es.notes:
            L.append(f"NOTE: {n}")
    if r.backfire is not None:
        b = r.backfire
        L += ["", "## Backfire hunt", "", f"- status: **{b.status}**; objective: {b.objective}"]
        L += [f"- baseline: {x}" for x in b.baseline.chain]
        L += [
            f"- evaluations: {b.evaluations_performed} of {b.max_evaluations}; "
            f"crossings found: {b.candidates_found}; rejected searches: {b.searches_rejected}"
        ]
        L += [
            f"- searched `{g.variable_id}` [{g.low}, {g.high}] {g.unit} ({g.source}): {g.behaviour}"
            for g in b.regions
        ]
        L += [f"- excluded `{e.variable_id}`: {e.reason}" for e in b.parameters_excluded]
        L += [f"- `{o.candidate_id}`: {o.fate} ({o.classification})" for o in b.outcomes]
        L += [f"- {b.strongest_finding_statement}"]
    L += ["", "## Candidates"]
    cls = {c.candidate_id: c.decision for c in r.classification}
    for c in r.candidates:
        dec = cls.get(c.candidate.candidate_id)
        ev = "-" if dec is None else dec.evidence_class
        L.append(
            f"- #{c.rank} {c.candidate.candidate_id} = {c.candidate.value:.6g} {c.candidate.unit}; classification: "
            f"{c.robustness_classification or 'not classified'}; evidence class: {ev}; evaluated: {c.evaluated}"
        )
    L += ["", "## How the candidates were challenged (settings actually used)"]
    a2 = r.analysis_settings
    if a2 is None:
        L.append("not available")
    else:
        L += [a2.scope_statement, "", f"settings applied: {a2.applied}; scheme: {a2.perturbation_scheme}", ""]
        L.append(f"falsification settings: {a2.falsification}")
        L.append(f"robustness settings: {a2.robustness}")
        L.append(f"evidence-challenge settings: {a2.evidence_challenge}")
        L.append("robustness perturbations applied:")
        for p in a2.robustness_perturbations_applied:
            L.append(
                f"- {p.get('variable_id')}: +/-{p.get('relative_half_width_percent')}% about "
                f"{p.get('baseline')} {p.get('unit')}, {p.get('n_evals')} evaluations, one at a time"
            )
        if not a2.robustness_perturbations_applied:
            L.append("- none")
        for n in a2.robustness_perturbations_skipped:
            L.append(f"- skipped: {n}")
        L += ["", f"settings audit: {a2.audit.status} ({a2.audit.n_warnings} warning(s))"]
        for f in a2.audit.findings:
            L.append(f"- [{f.severity}] {f.code}: {f.message}")
        L.append(a2.audit.note)
    L += ["", "## Verification"]
    v = r.verification_disclosure
    L.append(v.statement if v else "not available")
    L += ["", "## Computation"]
    b = r.budget_usage
    if b is None:
        L.append("not available")
    else:
        L += [
            b.statement,
            f"plan-time upper-bound estimate: {b.estimated_upper_bound_at_plan_time} (an estimate, not a measurement)",
            f"reproducibility re-run evaluations: {b.actual_model_evaluations_reproducibility_rerun}",
            f"scenarios: max {b.max_scenarios_used_by_any_candidate} used by any candidate "
            f"of {b.max_scenarios_per_candidate} allowed",
            b.counting_scope,
        ]
    L += ["", "## Limitations"] + [f"- {x}" for x in r.limitations] + [""]
    return "\n".join(L)
