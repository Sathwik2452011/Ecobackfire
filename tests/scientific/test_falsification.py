"""Outcomes of the pre-registered predictions and falsification attempts for E2 (docs/first-experiment.md 5, 7)."""

import pytest

from ecobackfire.experiments.runner import run_experiment
from ecobackfire.verification.suite import verify


@pytest.fixture(scope="module")
def rep(e2_config):
    return verify(e2_config, run_experiment(e2_config))


def get(rep, prefix):
    return next(c for c in rep.checks if c.check_id.startswith(prefix))


def test_F3_reversal_vanishes_when_demand_saturates_below_analytical_threshold(rep):
    c = get(rep, "F3")
    assert c.status == "limits_claim"
    by_smax = {row["s_max"]: row["reversal_found"] for row in c.details["rows"]}
    # analytical condition: (1-eta)*s_max > 1-r with eta=0.3, r=0.05 -> s_max > 0.95/0.7 = 1.3571...
    assert by_smax == {1.05: False, 1.2: False, 1.5: True, 2.0: True, 3.0: True}
    assert c.details["vanishes_for"] == [1.05, 1.2]


def test_F4_boundary_predictions_d_and_e_hold(rep, e2_config):
    c = get(rep, "F4")
    assert c.status == "passed" and c.details["predictions_d_e_checked"]
    rb = c.details["roots_by_boundary"]
    b1, b2, b3 = rb["B1"][0], rb["B2"][0], rb["B3"][0]
    assert b2 < b1 < b3  # early replacement lowers the threshold; ignoring embodied raises it
    assert b1 == pytest.approx(1.1127574680079595, rel=1e-9)


def test_F5_threshold_moves_over_illustrative_uncertainty_box_and_can_vanish(rep):
    c = get(rep, "F5")
    assert c.status == "limits_claim"
    assert c.details["corners_without_reversal"] > 0
    assert c.details["first_break_even_min"] < 1.1127574680079595 < c.details["first_break_even_max"]
    assert "ILLUSTRATIVE" in c.details["interval_values_are"]


def test_F1_F2_F8_survive(rep):
    for p in ("F1", "F2", "F6", "F8"):
        assert get(rep, p).status == "passed", p


def test_F7_evidence_gate_limits_the_claim(rep):
    c = get(rep, "F7")
    assert c.status == "limits_claim" and c.details["weakest_provenance"] == "illustrative"


def test_report_lists_narrowing_attempts_and_surviving_attempts(rep):
    assert any(s.startswith("F3") for s in rep.successful_falsifications)
    assert any(s.startswith("F1") for s in rep.failed_attempts_to_falsify)
    assert not any(s.startswith("F3") for s in rep.failed_attempts_to_falsify)
