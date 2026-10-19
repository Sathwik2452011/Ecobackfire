"""Backfire-hunt mission summary: counting and copying of results produced elsewhere (no search, scoring or rules).

Answers, for one mission: what was the baseline, what was searched, how many evaluations and crossings, which
candidates were falsified / survived falsification / survived robustness, which are evidence-sensitive, and what is
the strongest finding. Eliminated candidates are kept with their reasons; nothing is dropped.
"""

from __future__ import annotations

from ecobackfire.contracts.discovery import DiscoveryResult
from ecobackfire.contracts.mission import (
    BackfireMissionSummary,
    BackfireOutcome,
    BackfireSearchedRegion,
    ClassificationView,
    FalsificationView,
    InvestigationPlan,
    RankedCandidate,
    RobustnessView,
)

ELIMINATED = {"refuted", "numerically_unstable", "not_reproduced", "boundary_dependent", "eliminated_by_evidence"}
EVIDENCE_SENSITIVE = {"single_source_dependent", "evidence_boundary_dependent", "evidence_sensitive"}
LIMITED = {"evidence_insufficient", "inconclusive"}
SENSITIVE = {"sensitive"}


def fate_of(classification: str | None) -> str:
    if classification is None:
        return "not_assessed"
    if classification in ELIMINATED:
        return "eliminated"
    if classification == "robust":
        return "survived"
    if classification in SENSITIVE or classification in EVIDENCE_SENSITIVE:
        return "sensitive"
    if classification in LIMITED:
        return "survived_limited"
    return "not_assessed"


def build_backfire_summary(
    plan: InvestigationPlan,
    disc: DiscoveryResult,
    ranked: list[RankedCandidate],
    falsification: list[FalsificationView],
    robustness: list[RobustnessView],
    classification: list[ClassificationView],
    limitations: list[str],
) -> BackfireMissionSummary | None:
    rep = disc.backfire
    if rep is None:
        return None
    fal = {v.candidate_id: v for v in falsification}
    rob = {v.candidate_id: v for v in robustness}
    cls = {v.candidate_id: v.decision for v in classification}
    sources = {p.variable_id: p for p in (plan.backfire.participating if plan.backfire else [])}

    outcomes: list[BackfireOutcome] = []
    falsified: list[str] = []
    survived_f: list[str] = []
    survived_r: list[str] = []
    ev_robust: list[str] = []
    ev_sensitive: list[str] = []
    ev_insufficient: list[str] = []
    eliminated: list[str] = []
    for rc in ranked:  # evidence-aware order
        c = rc.candidate
        cid = c.candidate_id
        dec = cls.get(cid)
        fv, rv = fal.get(cid), rob.get(cid)
        f_failed = [] if fv is None else [x.check_id for x in fv.checks if x.status == "failed"]
        r_failed = [] if rv is None else [x.check_id for x in rv.checks if x.status == "failed"]
        fate = fate_of(None if dec is None else dec.classification)
        if fv is not None and fv.status == "assessed":
            (falsified if f_failed else survived_f).append(cid)
        if dec is not None:
            if dec.numerical_class == "robust":
                survived_r.append(cid)
            if dec.evidence_class == "evidence_robust":
                ev_robust.append(cid)
            elif dec.evidence_class in EVIDENCE_SENSITIVE:
                ev_sensitive.append(cid)
            elif dec.evidence_class == "evidence_insufficient":
                ev_insufficient.append(cid)
        if fate == "eliminated":
            eliminated.append(cid)
        outcomes.append(
            BackfireOutcome(
                candidate_id=cid,
                variable_id=c.variable_id,
                value=c.value,
                unit=c.unit,
                origin=c.backfire.origin if c.backfire is not None else "global_grid",
                rank=rc.rank,
                fate=fate,  # type: ignore[arg-type]
                classification=None if dec is None else dec.classification,
                evidence_class=None if dec is None else dec.evidence_class,
                falsification_failed_checks=f_failed,
                robustness_failed_checks=r_failed,
                reasons=[]
                if dec is None
                else list(dec.reasons)
                or ["not assessed: the candidate was outside the evaluated set or the analysis did not complete"],
            )
        )
        if dec is None:
            outcomes[-1] = outcomes[-1].model_copy(
                update={"reasons": ["not assessed: outside the evaluated set or the analysis did not complete"]}
            )

    regions = []
    for pr in rep.parameters:
        src = sources.get(pr.variable_id)
        n_rev = sum(
            1
            for iv in pr.intervals
            if iv.regime == "adverse" and (iv.lower_boundary != "range_edge" or iv.upper_boundary != "range_edge")
        )
        regions.append(
            BackfireSearchedRegion(
                variable_id=pr.variable_id,
                unit=pr.unit,
                low=pr.low,
                high=pr.high,
                grid_points=pr.grid_points,
                source=src.source if src else "mission_search",
                evidence_constraint=src.evidence_constraint if src else "",
                behaviour=pr.behaviour if pr.status == "searched" else pr.status,
                n_reversal_intervals=n_rev,
            )
        )

    strongest = next((o for o in outcomes if o.fate in ("survived", "survived_limited", "sensitive")), None)
    if strongest is None:
        stmt = (
            "No candidate survived the falsification, robustness and evidence challenges; "
            + (
                "no crossing was found in the searched regions."
                if not outcomes
                else "every found crossing was eliminated or not assessed (kept above with reasons)."
            )
        )
    else:
        stmt = (
            f"Strongest finding: {strongest.candidate_id} "
            f"({strongest.variable_id} = {strongest.value:.6g} {strongest.unit}), "
            f"classified '{strongest.classification}' (fate: {strongest.fate}). This is a model-derived, conditional "
            "break-even of the declared model under the declared inputs; it is not an empirical finding."
        )
    if rep.status == "budget_exhausted":
        stmt += " The search BUDGET WAS EXHAUSTED: this is a partial discovery and absence of findings is not evidence."
    n_assessed = sum(1 for o in outcomes if o.fate != "not_assessed")
    return BackfireMissionSummary(
        status=rep.status,
        objective=rep.objective,
        baseline=rep.baseline,
        parameters_searched=[r.variable_id for r in rep.parameters if r.status == "searched"],
        parameters_excluded=list(rep.excluded) + [
            e
            for e in (plan.backfire.excluded if plan.backfire else [])
            if e.variable_id not in {x.variable_id for x in rep.excluded}
        ],
        regions=regions,
        evaluations_performed=rep.evaluations_performed,
        max_evaluations=rep.max_evaluations,
        candidates_found=rep.n_candidates_found,
        candidates_from_refinement=rep.n_candidates_from_refinement,
        searches_rejected=rep.n_rejected,
        candidates_assessed=n_assessed,
        candidates_not_assessed=len(outcomes) - n_assessed,
        candidates_falsified=falsified,
        survived_falsification=survived_f,
        survived_robustness=survived_r,
        evidence_robust=ev_robust,
        evidence_sensitive=ev_sensitive,
        evidence_insufficient=ev_insufficient,
        eliminated=eliminated,
        outcomes=outcomes,
        strongest_finding=None if strongest is None else strongest.candidate_id,
        strongest_finding_statement=stmt,
        limitations=list(limitations),
    )
