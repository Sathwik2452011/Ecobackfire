"""Scoring criteria are pure stdlib functions; these tests deliberately import no third-party package."""

import math

from ecobackfire.discovery import scoring


def _raises(fn, *args) -> bool:  # type: ignore[no-untyped-def]
    try:
        fn(*args)
    except ValueError:
        return True
    return False


def test_proximity_hand_values() -> None:
    assert math.isclose(scoring.proximity_score(1.0, 0.5, 0.0, 3.0), 1 - 0.5 / 3)
    assert scoring.proximity_score(0.5, 0.5, 0.0, 3.0) == 1.0  # threshold at the operating point
    assert scoring.proximity_score(5.0, 0.0, 0.0, 3.0) == 0.0  # clipped, never negative
    assert scoring.proximity_score(-9.0, 0.0, 0.0, 3.0) == 0.0


def test_proximity_rejects_bad_inputs() -> None:
    assert _raises(scoring.proximity_score, 1.0, 0.5, 3.0, 3.0)
    assert _raises(scoring.proximity_score, 1.0, 0.5, 3.0, 0.0)
    assert _raises(scoring.proximity_score, float("nan"), 0.5, 0.0, 3.0)


def test_magnitude_hand_values_and_monotonic() -> None:
    assert scoring.magnitude_score(0.0, 1.0) == 0.0
    assert scoring.magnitude_score(1.0, 1.0) == 0.5
    assert math.isclose(scoring.magnitude_score(9.0, 1.0), 0.9)
    vals = [scoring.magnitude_score(s, 10.0) for s in (0.0, 1.0, 10.0, 1e3, 1e9)]
    assert vals == sorted(vals) and all(0.0 <= v < 1.0 for v in vals)


def test_magnitude_rejects_bad_inputs() -> None:
    assert _raises(scoring.magnitude_score, -1.0, 1.0)
    assert _raises(scoring.magnitude_score, 1.0, 0.0)
    assert _raises(scoring.magnitude_score, float("inf"), 1.0)


def test_evidence_score_policy() -> None:
    assert scoring.evidence_score(["illustrative"] * 8) == 0.0
    assert math.isclose(scoring.evidence_score(["measured", "illustrative"]), 0.4)
    assert math.isclose(scoring.evidence_score(["user_supplied", "estimated"]), 0.3)
    assert scoring.evidence_score([]) == 0.0
    assert _raises(scoring.evidence_score, ["made_up"])


def test_evidence_policy_never_ranks_unreviewed_citation_above_1() -> None:
    assert max(scoring.EVIDENCE_WEIGHT.values()) < 1.0
    assert scoring.EVIDENCE_WEIGHT["illustrative"] == min(scoring.EVIDENCE_WEIGHT.values()) == 0.0


def test_combine() -> None:
    w = {"proximity": 0.4, "magnitude": 0.3, "evidence": 0.3}
    assert math.isclose(scoring.combine({"proximity": 1.0, "magnitude": 0.5, "evidence": 0.0}, w), 0.55)
    assert _raises(scoring.combine, {"proximity": 1.0}, w)  # mismatched keys
    assert _raises(scoring.combine, {"proximity": 1.0, "magnitude": 0.5, "evidence": 0.0}, {**w, "evidence": 0.5})
    assert _raises(scoring.combine, {"proximity": 1.5, "magnitude": 0.5, "evidence": 0.0}, w)  # out of range


def test_scoring_is_deterministic() -> None:
    a = [scoring.proximity_score(1.1127574680, 0.5, 0.0, 3.0) for _ in range(3)]
    assert a[0] == a[1] == a[2]
