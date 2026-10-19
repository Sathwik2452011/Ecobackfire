"""Transparent candidate-scoring criteria. Pure functions, stdlib only (no pydantic) so they are
unit-testable anywhere.

Each criterion returns a value in [0, 1]. The total is a weighted sum with explicit weights. The
score ranks candidates for human attention; it is NOT a measure of scientific truth or importance.

Criteria applied in this step
  proximity  1 - |threshold - base| / (range width), clipped to [0, 1]. A threshold near the current
             operating point is more decision-relevant than one at the far edge of the range.
  magnitude  s/(1+s) with s = (max g - min g over the scan) / reference emissions scale. Larger swings
             of the decision metric across the range mean the reversal is not a small-number effect.
  evidence   mean over all required inputs of EVIDENCE_WEIGHT[provenance class]. All-illustrative
             inputs score 0.  THIS IS EVIDENCE DOCUMENTATION, NOT VALIDITY: it measures how well the inputs
             are documented (provenance class), nothing else. A sourced input raises it; that does not make
             the finding more likely to be true and does not say the finding is robust. See "Three axes".

Three axes (kept separate on purpose; see ``score_axes``)
  evidence_documentation  numeric, in [0, 1]; the ``evidence`` component above. Only axis that enters ``total``.
  scientific_validity     NEVER numeric and never produced by this module: always ``not_assessed_by_scoring``.
                          Whether a modeled finding describes the real world needs independent empirical
                          validation that no score here can supply. While any required input is assumed or
                          illustrative the finding is additionally labeled ``conditional_on_unevidenced_inputs``.
  candidate_robustness    categorical, from the candidate's classification (``classification.py``): how the
                          modeled finding behaved under numerical, parameter and evidence challenges. It is not in
                          ``total``; it GATES ranking (``rank_key``): a fragile or evidence-eliminated candidate
                          never outranks a robust one because its inputs are better documented.

Not yet applied (reported as such in every result): robustness under input uncertainty and
independent verification / falsification (Slice D).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

#: Policy table (a modelling choice, not a measurement). measured/sourced are capped below 1 because
#: an unreviewed citation is not verified; review gating arrives with Slice D.
EVIDENCE_WEIGHT: dict[str, float] = {
    "measured": 0.8,
    "sourced": 0.8,
    "estimated": 0.4,
    "simulated": 0.3,
    "user_supplied": 0.2,
    "illustrative": 0.0,
}

FORMULA = "total = w_proximity*proximity + w_magnitude*magnitude + w_evidence*evidence"


def proximity_score(root: float, base: float, lo: float, hi: float) -> float:
    if not all(math.isfinite(x) for x in (root, base, lo, hi)):
        raise ValueError("proximity inputs must be finite")
    if not lo < hi:
        raise ValueError("proximity requires lo < hi")
    return min(1.0, max(0.0, 1.0 - abs(root - base) / (hi - lo)))


def magnitude_score(swing: float, scale: float) -> float:
    if not (math.isfinite(swing) and math.isfinite(scale)):
        raise ValueError("magnitude inputs must be finite")
    if swing < 0 or scale <= 0:
        raise ValueError("magnitude requires swing >= 0 and scale > 0")
    s = swing / scale
    return s / (1.0 + s)


def evidence_score(classes: Sequence[str]) -> float:
    if not classes:
        return 0.0
    unknown = sorted({c for c in classes if c not in EVIDENCE_WEIGHT})
    if unknown:
        raise ValueError(f"unknown provenance class(es): {unknown}")
    return sum(EVIDENCE_WEIGHT[c] for c in classes) / len(classes)


def combine(components: Mapping[str, float], weights: Mapping[str, float]) -> float:
    if set(components) != set(weights):
        raise ValueError(f"components {sorted(components)} and weights {sorted(weights)} must match")
    if not math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9):
        raise ValueError("weights must sum to 1")
    for k, v in components.items():
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"component {k}={v} outside [0, 1]")
    return sum(weights[k] * components[k] for k in components)


# ------------------------------------------------------------------ three axes (evidence-sensitive ranking)

#: Robustness tier of a candidate classification; LOWER ranks first. Policy table, not a measurement.
#: 0 survived the challenges; 1 not (yet) assessable; 2 sensitive/limited; 3 refuted by a challenge.
ROBUSTNESS_TIER: dict[str, int] = {
    "robust": 0,
    "inconclusive": 1,
    "evidence_insufficient": 1,
    "sensitive": 2,
    "boundary_dependent": 2,
    "evidence_sensitive": 2,
    "evidence_boundary_dependent": 2,
    "single_source_dependent": 2,
    "refuted": 3,
    "numerically_unstable": 3,
    "not_reproduced": 3,
    "eliminated_by_evidence": 3,
}

VALIDITY_NOT_ASSESSED = "not_assessed_by_scoring"
CONDITIONAL_NOTE = "conditional_on_unevidenced_inputs"
RANK_RULE = "rank_key = (robustness tier of the classification, -total); lower sorts first; ties by candidate id"


def score_axes(provenance_classes: Sequence[str], classification: str) -> dict[str, object]:
    """The three axes for one candidate, never merged. ``provenance_classes``: one per REQUIRED input."""
    if classification not in ROBUSTNESS_TIER:
        raise ValueError(f"unknown classification {classification!r}")
    documented = sum(1 for c in provenance_classes if c in ("measured", "sourced"))
    unevidenced = sorted({c for c in provenance_classes if c not in ("measured", "sourced")})
    return {
        "evidence_documentation": {
            "score": evidence_score(provenance_classes),
            "n_required_inputs": len(provenance_classes),
            "n_measured_or_sourced": documented,
            "classes_without_evidence": unevidenced,
            "meaning": "how well the inputs are documented; not validity and not robustness",
        },
        "scientific_validity": VALIDITY_NOT_ASSESSED,
        "scientific_validity_note": CONDITIONAL_NOTE if unevidenced else "all required inputs are documented; "
        "this still does not establish that the finding describes the real world",
        "candidate_robustness": {"classification": classification, "tier": ROBUSTNESS_TIER[classification]},
    }


def rank_key(total: float, classification: str, candidate_id: str = "") -> tuple[int, float, str]:
    """Sort key: robustness tier first, then higher attention score, then id (deterministic)."""
    if classification not in ROBUSTNESS_TIER:
        raise ValueError(f"unknown classification {classification!r}")
    if not math.isfinite(total):
        raise ValueError("total must be finite")
    return (ROBUSTNESS_TIER[classification], -total, candidate_id)


# ------------------------------------------------------------------ backfire hunt: numerical well-definedness

FORMULA_BACKFIRE = (
    "total = (1 - w_wd) * (w_proximity*proximity + w_magnitude*magnitude + w_evidence*evidence) + w_wd * well_defined"
)

#: Policy thresholds of the well-definedness criteria (modelling choices, recorded in every result).
WELL_DEFINED_POLICY: dict[str, float] = {
    "residual_rel_tol": 1e-9,  # |g(root)| <= tol * reference scale
    "transversal_rel_tol": 1e-6,  # both bracket end values are at least tol * reference scale away from zero
    "edge_margin": 0.02,  # root at least this fraction of the searched range from either end
}


def well_defined_components(
    *,
    converged: bool,
    residual: float,
    g_low: float | None,
    g_high: float | None,
    scale: float,
    root: float,
    lo: float,
    hi: float,
    within_physical_bounds: bool = True,
) -> dict[str, float]:
    """Four binary criteria in {0, 1}: they describe how well-defined the NUMERICAL crossing is, not how interesting,
    robust or true it is (those are scored/classified elsewhere). ``well_defined`` is their mean."""
    if scale <= 0 or not lo < hi:
        raise ValueError("well-definedness needs scale > 0 and lo < hi")
    p = WELL_DEFINED_POLICY
    ends = [v for v in (g_low, g_high) if v is not None]
    comp = {
        "converged_to_zero": 1.0 if converged and abs(residual) <= p["residual_rel_tol"] * scale else 0.0,
        "transversal": 1.0 if ends and all(abs(v) >= p["transversal_rel_tol"] * scale for v in ends) else 0.0,
        "interior": (
            1.0 if (root - lo) / (hi - lo) >= p["edge_margin"] and (hi - root) / (hi - lo) >= p["edge_margin"] else 0.0
        ),
        "within_physical_bounds": 1.0 if within_physical_bounds else 0.0,
    }
    comp["well_defined"] = sum(comp.values()) / 4.0
    return comp
