"""The labeling gate: a PURE function from check results to a label (scientific-method.md section 5).

Nothing here can produce 'independently_reproduced' or 'established'; those are human-curator states.
"""

from __future__ import annotations

from collections.abc import Sequence

from ecobackfire.contracts.verification import CheckResult, Label

# Checks in these families, if they FAIL, refute the claim as stated. Failures elsewhere mean the
# implementation or numerics cannot be trusted, i.e. the claim is unsupported (inconclusive).
REFUTING_FAMILIES = frozenset({"falsification_perturbation", "falsification_counterexample"})

# A claim can only be 'verified_internal' if every one of these families was actually run and passed.
# (Prevents vacuous verification when checks are skipped or not applicable.)
MUST_PASS_FAMILIES = frozenset(
    {
        "independent_recomputation",
        "invariants",
        "benchmarks",
        "edge",
        "stability",
        "falsification_perturbation",
        "falsification_counterexample",
    }
)

LABEL_DEFINITIONS: dict[str, str] = {
    "not_applicable": "No candidate finding was produced, so nothing was labeled.",
    "unverified_hypothesis": "A statement with falsification criteria; no test has been run.",
    "candidate": "An experiment appears to support the statement; verification is incomplete.",
    "conditional": (
        "Survived this project's checks only under stated conditions, or because key inputs are "
        "illustrative/unsourced. It is not a real-world conclusion."
    ),
    "verified_internal": (
        "Survived this project's own attempts to break it, with the evidence it had. "
        "It is not a claim of truth, novelty, or independent reproduction."
    ),
    "refuted": "At least one required falsification test contradicted the claim as stated.",
    "inconclusive": "Required checks failed numerically, were skipped, or did not run.",
}


def label_from_checks(checks: Sequence[CheckResult], has_claim: bool) -> tuple[Label, list[str]]:
    just: list[str] = []
    failed = [c for c in checks if c.required and c.status == "failed"]
    unresolved = [c for c in checks if c.required and c.status in ("skipped", "inconclusive")]
    limits = [c for c in checks if c.status == "limits_claim"]

    if not has_claim:
        just.append("No candidate finding exists (no break-even/reversal was found or none was requested).")
        for c in failed:
            just.append(f"NOTE: required check {c.check_id} FAILED: {c.summary}")
        return "not_applicable", just

    if failed:
        refuting = [c for c in failed if c.family in REFUTING_FAMILIES]
        for c in failed:
            just.append(f"rule 1: required check {c.check_id} ({c.family}) FAILED: {c.summary}")
        if refuting:
            return "refuted", just
        return "inconclusive", just

    if unresolved:
        for c in unresolved:
            just.append(f"rule 2: required check {c.check_id} ({c.family}) is {c.status}: {c.summary}")
        return "inconclusive", just

    passed_families = {c.family for c in checks if c.status == "passed"}
    missing = sorted(MUST_PASS_FAMILIES - passed_families)
    if missing:
        just.append(f"rule 2b: required check families did not run/pass: {missing}")
        return "inconclusive", just

    if limits:
        for c in limits:
            just.append(f"rule 3: {c.check_id} ({c.family}) limits the claim: {c.summary}")
        return "conditional", just

    just.append("rule 5: all required checks passed, no check limits the claim.")
    return "verified_internal", just
