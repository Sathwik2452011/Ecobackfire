"""Evidence-aware candidate classification (layer 4, pure; contracts + stdlib only).

Two axes, then one combined class; every rule is categorical and listed in ``RULE_DEFINITIONS``. There are NO
numeric thresholds or percentages here: the only quantities are counts of scenarios/checks and which sources
they belong to.

Numerical axis (from falsification checks X1-X6 and the parameter-perturbation challenge X5)
Evidence axis (from the evaluated evidence scenarios; see ``classify_evidence``)
Combined (``combine``): numerics first, then evidence, then parameter sensitivity, then insufficiency.

A classification describes how a MODELED finding behaved under these challenges. It is not a probability that
the finding is true and not a claim about the real world.
"""

from __future__ import annotations

from collections.abc import Sequence

from ecobackfire.contracts.evidence_sensitivity import (
    CHALLENGE_TYPES,
    LOST_KINDS,
    PRESERVED_KINDS,
    UNRESOLVED_KINDS,
    CandidateClass,
    ChallengeResult,
    ChallengeType,
    ClassificationDecision,
    EvidenceClass,
    NumericalClass,
    ScenarioOutcome,
)

#: falsification check id -> the type of challenge it is. Evidence checks carry their own type.
CHECK_CHALLENGE_TYPE: dict[str, ChallengeType] = {
    "X1_crossing_validity": "numerical_falsification",
    "X2_numerical_stability": "numerical_falsification",
    "X3_boundary_dependence": "numerical_falsification",
    "X4_reproducibility": "numerical_falsification",
    "X6_independent_roots": "numerical_falsification",
    "X5_perturbation": "parameter_perturbation",
    "R1_parameter_ranges": "parameter_perturbation",
    "E1_evidence_range": "evidence_range_sensitivity",
    "E2_source_disagreement": "source_disagreement",
}


def challenge_type_of(check_id: str) -> ChallengeType:
    try:
        return CHECK_CHALLENGE_TYPE[check_id]
    except KeyError:
        raise ValueError(f"unknown check id {check_id!r}: its challenge type is not defined") from None


def _by_id(challenges: Sequence[ChallengeResult]) -> dict[str, ChallengeResult]:
    return {c.check_id: c for c in challenges}


# ------------------------------------------------------------------ numerical axis


def classify_numerical(challenges: Sequence[ChallengeResult]) -> tuple[NumericalClass, str, list[str]]:
    """Numerical + parameter axis. Returns (class, rule id, reasons). Evidence challenges are ignored here."""
    c = _by_id([x for x in challenges if x.challenge_type in ("numerical_falsification", "parameter_perturbation")])

    def st(cid: str) -> str:
        return c[cid].status if cid in c else "skipped"

    def why(cid: str) -> str:
        return f"{cid}: {c[cid].summary}"

    if st("X1_crossing_validity") == "failed":
        return "refuted", "N1", [why("X1_crossing_validity")]
    if st("X2_numerical_stability") == "failed":
        return "numerically_unstable", "N2", [why("X2_numerical_stability")]
    nr = [cid for cid in ("X4_reproducibility", "X6_independent_roots") if st(cid) == "failed"]
    if nr:
        return "not_reproduced", "N3", [why(x) for x in nr]
    if st("X3_boundary_dependence") == "failed":
        return "boundary_dependent", "N4", [why("X3_boundary_dependence")]
    sens = [cid for cid in ("X5_perturbation", "R1_parameter_ranges") if st(cid) == "failed"]
    lim_ids = ("X5_perturbation", "R1_parameter_ranges", "X3_boundary_dependence")
    lim = [cid for cid in lim_ids if st(cid) == "limits_claim"]
    if sens or lim:
        return "sensitive", "N5", [why(x) for x in sens + lim]
    core = ("X1_crossing_validity", "X2_numerical_stability", "X3_boundary_dependence", "X4_reproducibility")
    not_passed = [x for x in core if st(x) != "passed"]
    param_assessed = "passed" in (st("X5_perturbation"), st("R1_parameter_ranges"))
    if not_passed or not param_assessed:
        reasons = [f"{x} is {st(x)}" for x in not_passed]
        if not param_assessed:
            reasons.append(f"X5_perturbation is {st('X5_perturbation')}: parameter robustness is not assessed")
        return "inconclusive", "N6", reasons
    note = "X1-X4 and the parameter-perturbation challenge passed"
    if st("X6_independent_roots") in ("skipped", "inconclusive"):
        note += "; X6 (independent roots) was not assessed"
    return "robust", "N7", [note]


# ------------------------------------------------------------------ evidence axis

_ENDPOINT_ROLES = ("lower", "upper")


def classify_evidence(outcomes: Sequence[ScenarioOutcome]) -> tuple[EvidenceClass, str, list[str]]:
    """Evidence axis from the evaluated scenarios (see RULE_DEFINITIONS E0-E6)."""
    challenging = [o for o in outcomes if not o.is_baseline]
    if not challenging:
        return (
            "evidence_insufficient",
            "E0",
            ["no documented evidence variation exists beyond the candidate's own base value "
             f"({len(outcomes)} scenario(s) evaluated, all equal to the base input)"],
        )
    kinds = [o.outcome for o in outcomes]
    n_p = sum(k in PRESERVED_KINDS for k in kinds)
    n_l = sum(k in LOST_KINDS for k in kinds)
    n_u = sum(k in UNRESOLVED_KINDS for k in kinds)
    counts = f"{len(outcomes)} scenario(s): {n_p} preserved, {n_l} lost, {n_u} unresolved"
    if n_l == 0 and n_u == 0:
        return "evidence_robust", "E1", [f"every evaluated scenario preserved the candidate ({counts})"]
    if n_l == 0:
        unresolved = [o.scenario_id for o in outcomes if o.outcome in UNRESOLVED_KINDS]
        return "evidence_inconclusive", "E2", [f"{counts}; unresolved: {unresolved}"]
    if n_p == 0:
        if n_u == 0:
            return "eliminated_by_evidence", "E3", [f"no scenario preserved the candidate ({counts})"]
        return "evidence_inconclusive", "E3b", [f"{counts}; unresolved scenarios prevent a verdict"]
    # mixed
    preserved = [o for o in outcomes if o.outcome in PRESERVED_KINDS]
    non_endpoint = [o for o in outcomes if o.role not in _ENDPOINT_ROLES]
    only_endpoints = all(o.role in _ENDPOINT_ROLES for o in preserved)
    if only_endpoints and non_endpoint and all(o.outcome in LOST_KINDS for o in non_endpoint):
        return (
            "evidence_boundary_dependent",
            "E4",
            [f"preserved only at documented range endpoints {[o.scenario_id for o in preserved]} and lost at every "
             f"non-endpoint scenario ({counts})"],
        )
    sources = sorted({o.source_key for o in outcomes})
    kept = sorted({o.source_key for o in preserved})
    if len(sources) >= 2 and len(kept) == 1:
        return (
            "single_source_dependent",
            "E5",
            [f"only source {kept[0]!r} has preserved scenarios; the other source(s) "
             f"{[s for s in sources if s not in kept]} have none ({counts})"],
        )
    lost_ids = [o.scenario_id for o in outcomes if o.outcome in LOST_KINDS]
    return "evidence_sensitive", "E6", [f"scenarios disagree ({counts}); lost: {lost_ids}"]


# ------------------------------------------------------------------ combination


def combine(
    numerical: tuple[NumericalClass, str, list[str]],
    evidence: tuple[EvidenceClass, str, list[str]],
    challenges: Sequence[ChallengeResult],
) -> ClassificationDecision:
    ncls, nrule, nwhy = numerical
    ecls, erule, ewhy = evidence
    final: CandidateClass
    reasons: list[str] = []
    if ncls in ("refuted", "numerically_unstable", "not_reproduced"):
        final, rule = ncls, "C1"
        reasons = [f"numerical axis ({nrule}): {r}" for r in nwhy]
    elif ncls == "boundary_dependent":
        final, rule = "boundary_dependent", "C2"
        reasons = [f"numerical axis ({nrule}): {r}" for r in nwhy]
    elif ecls in (
        "eliminated_by_evidence",
        "single_source_dependent",
        "evidence_boundary_dependent",
        "evidence_sensitive",
    ):
        final, rule = ecls, "C3"
        reasons = [f"evidence axis ({erule}): {r}" for r in ewhy]
        if ncls == "sensitive":
            reasons.append("the numerical/parameter axis is also 'sensitive' (reported separately)")
    elif ncls == "sensitive":
        final, rule = "sensitive", "C4"
        reasons = [f"numerical axis ({nrule}): {r}" for r in nwhy]
    elif ncls == "inconclusive" or ecls == "evidence_inconclusive":
        final, rule = "inconclusive", "C5"
        if ncls == "inconclusive":
            reasons += [f"numerical axis ({nrule}): {r}" for r in nwhy]
        if ecls == "evidence_inconclusive":
            reasons += [f"evidence axis ({erule}): {r}" for r in ewhy]
    elif ecls == "evidence_insufficient":
        final, rule = "evidence_insufficient", "C6"
        reasons = [f"evidence axis ({erule}): {r}" for r in ewhy]
        reasons.append("numerically the finding held, but no documented evidence variation exists to test it against")
    else:
        final, rule = "robust", "C7"
        reasons = [f"numerical axis ({nrule}): {r}" for r in nwhy] + [f"evidence axis ({erule}): {r}" for r in ewhy]
    def types_with(status: str) -> list[ChallengeType]:
        hit = {c.challenge_type for c in challenges if c.status == status}
        return [t for t in CHALLENGE_TYPES if t in hit]  # type: ignore[misc]

    failed, limiting = types_with("failed"), types_with("limits_claim")
    not_assessed = sorted(f"{c.check_id} ({c.status})" for c in challenges if c.status in ("skipped", "inconclusive"))
    return ClassificationDecision(
        numerical_class=ncls,
        evidence_class=ecls,
        classification=final,
        rule_id=rule,
        reasons=reasons,
        failed_challenge_types=failed,
        limiting_challenge_types=limiting,
        not_assessed=not_assessed,
    )


def classify(challenges: Sequence[ChallengeResult], outcomes: Sequence[ScenarioOutcome]) -> ClassificationDecision:
    return combine(classify_numerical(challenges), classify_evidence(outcomes), challenges)
