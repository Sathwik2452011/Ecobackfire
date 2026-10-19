"""Evidence-sensitive evaluation, falsification/robustness integration, classification. SYNTHETIC fixtures.
Model: g(x) = s*(x - 0.5*h) ; candidate = root of g in [0,1]; beneficial side flips with s.
Pure: no pint, no pytest import."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _evidence_sensitivity_fixtures import UNIT, record, resolution

from ecobackfire.contracts.evidence_sensitivity import ChallengeResult, EvidenceSensitivityConfig, ScenarioOutcome
from ecobackfire.discovery.classification import classify, classify_evidence, classify_numerical
from ecobackfire.discovery.evidence_scenarios import build_scenario_set
from ecobackfire.discovery.evidence_sensitivity import evaluate_candidate_evidence
from ecobackfire.discovery.robustness import PerturbationSpec
from ecobackfire.errors import ConfigError, PhysicalConstraintError
from ecobackfire.experiments.breakeven import find_breakevens

N, XT = 98, 1e-12


def _h(q):  # effective scale of the root; trap: a "hole" at q in (1.13, 1.17) pushes the root out of the window
    return 5.0 if abs(q - 1.15) < 0.02 else q


def make(over):
    q, s, p = over.get("q", 1.0), over.get("s", 1.0), over.get("p", 1.0)
    if q < 0:
        def bad(x):
            raise PhysicalConstraintError("q negative")
        return bad
    hq = _h(q) * (5.0 if abs(p - 1.0) < 0.005 else 1.0) ** 0  # p: see make_p
    return lambda x: s * (x - 0.5 * hq)


def make_p(over):
    """Parameter p is fragile: a 1% change of p loses the candidate (non-evidence perturbation)."""
    q = over.get("q", 1.0)
    p = over.get("p", 1.0)
    return lambda x: x - 0.5 * q * (1.0 if abs(p - 1.0) < 1e-9 else 5.0)


def _root():
    return find_breakevens(make({}), 0, 1, N, XT).roots[0]


def run(sets, base=None, mk=make, **kw):
    return evaluate_candidate_evidence(
        candidate_id="c1", model_ref="lin@1", make_g=mk, base=base or {"q": 1.0}, search_var="x", unit="1",
        root=_root(), lo=0, hi=1, grid_points=N, xtol=XT, scenario_sets=sets, **kw)


def S(*recs, select=None, param="q"):
    return build_scenario_set(resolution(param, list(recs), select=select), UNIT)


def test_survives_numerical_and_evidence_variation():
    a, b = record("a", 1.0, low=0.9, high=1.04), record("b", 1.2, source="srcB")
    ev = run([S(a, b, select=a.evidence_id)], perturb_vars=["q"])
    assert ev.decision.classification == "robust", ev.decision
    assert ev.decision.evidence_class == "evidence_robust" and ev.deterministic
    assert [d.status for d in ev.dimensions] == ["held", "held", "held"]
    assert ev.decision.failed_challenge_types == []


def test_fails_on_documented_range_and_names_the_challenge_type():
    a = record("a", 1.0, low=0.9, high=2.5)  # q=2.5 puts the root at 1.25, outside the window
    ev = run([S(a)], perturb_vars=["q"])
    assert ev.decision.classification == "evidence_sensitive"
    assert ev.decision.failed_challenge_types == ["evidence_range_sensitivity"]
    lost = [o for o in ev.outcomes if o.lost]
    assert [o.role for o in lost] == ["upper"] and lost[0].outcome == "disappeared_always_beneficial"
    assert ev.dimensions[2].status == "not_held" and ev.dimensions[0].status == "held"


def test_single_source_dependence():
    a, b = record("a", 1.0, source="A"), record("b", 2.5, source="B")
    ev = run([S(a, b, select=a.evidence_id)])
    assert ev.decision.classification == "single_source_dependent" and ev.decision.rule_id == "C3"
    assert ev.decision.failed_challenge_types == ["source_disagreement"]


def test_all_sources_eliminate_the_finding():
    a, b = record("a", 2.5, source="A"), record("b", 3.0, source="B")
    ev = run([S(a, b)])  # conflicting, base q=1 is not from evidence
    assert ev.decision.evidence_class == "eliminated_by_evidence"
    assert ev.decision.classification == "eliminated_by_evidence"


def test_exists_only_at_an_evidence_boundary():
    a = record("a", 1.15, low=1.0, high=1.3)  # central 1.15 hits the hole; the endpoints keep the finding
    ev = run([S(a)])
    roles = {o.role: o.outcome for o in ev.outcomes}
    assert roles == {"lower": "preserved", "central": "disappeared_always_beneficial", "upper": "preserved"}
    assert ev.decision.classification == "evidence_boundary_dependent" and ev.decision.rule_id == "C3"


def test_evidence_insufficient_when_only_the_base_value_exists():
    ev = run([S(record("a", 1.0))], perturb_vars=["q"])
    assert ev.decision.evidence_class == "evidence_insufficient"
    assert ev.decision.classification == "evidence_insufficient" and ev.decision.numerical_class == "robust"
    assert ev.dimensions[2].status == "not_assessed"
    ev0 = run([], perturb_vars=["q"])
    assert ev0.decision.classification == "evidence_insufficient"
    # without a parameter-perturbation challenge the numerical axis is inconclusive (C5 precedes C6)
    assert run([]).decision.classification == "inconclusive"


def test_missing_evidence_is_insufficient_and_listed_as_limitation():
    ev = run([build_scenario_set(resolution("q", []), UNIT)], perturb_vars=["q"])
    assert ev.decision.classification == "evidence_insufficient"
    assert any("evidence missing" in x for x in ev.limitations)


def test_scenario_near_the_search_edge_is_unresolved_not_a_pass():
    ev = run([S(record("a", 1.0, low=0.01, high=1.0))])  # root 0.005 is within edge_margin of the window
    o = {x.role: x for x in ev.outcomes}
    assert o["lower"].outcome == "preserved_near_search_edge"
    assert ev.decision.evidence_class == "evidence_inconclusive" and ev.decision.classification == "inconclusive"


def test_model_failure_in_a_scenario_is_invalid_and_inconclusive():
    ev = run([S(record("a", 1.0, low=-0.1, high=1.0))])
    assert {x.role: x.outcome for x in ev.outcomes}["lower"] == "invalid"
    assert ev.decision.classification == "inconclusive"


def test_reversed_beneficial_side_counts_as_loss():
    ev = run([S(record("a", 1.0), param="s")], base={"q": 1.0, "s": 1.0})  # placeholder param s=1 == base
    assert ev.decision.evidence_class == "evidence_insufficient"
    ev = run([S(record("a", -1.0), param="s")], base={"q": 1.0, "s": 1.0})
    assert ev.outcomes[0].outcome == "reversed" and ev.decision.classification == "eliminated_by_evidence"


def test_parameter_perturbation_failure_is_distinct_from_evidence_failure():
    a = record("a", 1.0, low=0.9, high=1.04)
    ev = run([S(a)], base={"q": 1.0, "p": 1.0}, mk=make_p, perturb_vars=["p"])
    assert ev.decision.failed_challenge_types == ["parameter_perturbation"]
    assert ev.decision.evidence_class == "evidence_robust" and ev.decision.classification == "sensitive"
    assert ev.decision.rule_id == "C4"


def test_configured_parameter_ranges_are_a_separate_dimension():
    spec = PerturbationSpec(name="q", unit="1", baseline=1.0, low=0.9, high=1.04, n_evals=5, basis="configured")
    ev = run([S(record("a", 1.0))], robustness_perturbations=[spec])
    byd = {d.dimension: d for d in ev.dimensions}
    assert byd["parameter"].status == "held" and byd["parameter"].n_cases > 0
    assert byd["evidence"].status == "not_assessed"
    assert ev.robustness_report_sha256 and ev.robustness_settings["perturbations"]


def test_provenance_chain_is_preserved_end_to_end():
    a, b = record("a", 1.0, low=0.9, high=1.04, source="A"), record("b", 1.2, source="B")
    ev = run([S(a, b, select=a.evidence_id)])
    sc = {s.scenario_id: s for st in ev.scenario_sets for s in st.scenarios}
    assert len(ev.outcomes) == len(sc) == 4
    for o in ev.outcomes:
        s = sc[o.scenario_id]
        assert (o.dataset_id, o.dataset_version, o.evidence_id, o.source_key, o.value, o.unit) == (
            s.dataset_id, s.dataset_version, s.evidence_id, s.source_key, s.value, s.unit)
    assert ev.candidate_id == "c1" and ev.model_ref == "lin@1" and ev.scenario_sets[0].resolution_decision
    assert ev.decision.classification and ev.falsification_report_sha256 and ev.falsification_settings


def test_repeated_evaluation_is_identical():
    a = record("a", 1.0, low=0.9, high=2.5)
    assert run([S(a)]).model_dump() == run([S(a)]).model_dump()


def test_configuration_errors_are_loud():
    def raises(fn):
        try:
            fn()
        except ConfigError:
            return True
        return False
    assert raises(lambda: run([S(record("a", 1.0), param="zzz")]))
    assert raises(lambda: run([S(record("a", 1.0), param="x")], base={"q": 1.0, "x": 0.1}))
    assert raises(lambda: run([S(record("a", 1.0))], expected_units={"q": "kg"}))
    assert raises(lambda: run([S(record("a", 1.0), param="q"), S(record("b", 1.0), param="q")]))
    assert raises(
        lambda: run([S(record("a", 1.0, low=0.9, high=1.0))], config=EvidenceSensitivityConfig(max_scenarios=2))
    )


def _o(role, kind, src="A", sid=None, base=False):
    return ScenarioOutcome(scenario_id=sid or f"q|{src}|{role}", parameter="q", role=role, evidence_id=src,
                           source_key=src,
                           dataset_id="d", dataset_version="1", value=1.0, unit="1", outcome=kind, is_baseline=base,
                           root=None, beneficial_side=None, root_shift_rel=None, edge_margin=None, n_roots=0, reason="",
                           challenge_type=(
                               "evidence_range_sensitivity" if role in ("lower", "upper") else "source_disagreement"
                           ))


def _c(cid, status, typ):
    return ChallengeResult(challenge_type=typ, check_id=cid, status=status, category="x", summary=f"{cid} {status}")


ALL_OK = [_c(c, "passed", "numerical_falsification") for c in
          ("X1_crossing_validity", "X2_numerical_stability", "X3_boundary_dependence", "X4_reproducibility")] + \
         [_c("X5_perturbation", "passed", "parameter_perturbation")]


def test_numerical_failures_take_precedence_over_evidence():
    ok_ev = [_o("source", "preserved"), _o("lower", "preserved")]
    for cid, want in (("X1_crossing_validity", "refuted"), ("X2_numerical_stability", "numerically_unstable"),
                      ("X4_reproducibility", "not_reproduced"), ("X3_boundary_dependence", "boundary_dependent")):
        ch = [_c(cid, "failed", "numerical_falsification") if c.check_id == cid else c for c in ALL_OK]
        assert classify(ch, ok_ev).classification == want, cid
        assert classify(ch, [_o("source", "disappeared_other")]).classification == want


def test_classification_truth_table_evidence_axis():
    assert classify_evidence([_o("source", "preserved", base=True)])[0] == "evidence_insufficient"
    assert classify_evidence([_o("source", "preserved"), _o("lower", "preserved")])[0] == "evidence_robust"
    assert classify_evidence([_o("source", "preserved"), _o("lower", "invalid")])[0] == "evidence_inconclusive"
    assert classify_evidence([_o("source", "disappeared_other")])[0] == "eliminated_by_evidence"
    assert classify_evidence([_o("source", "invalid"), _o("lower", "disappeared_other")])[0] == "evidence_inconclusive"
    mixed = [_o("source", "preserved", "A"), _o("source", "disappeared_other", "B")]
    assert classify_evidence(mixed)[0] == "single_source_dependent"
    mixed2 = [_o("source", "preserved", "A"), _o("source", "preserved", "B"), _o("source", "disappeared_other", "C")]
    assert classify_evidence(mixed2)[0] == "evidence_sensitive"  # two sources keep it: not single-source
    one_source = [_o("lower", "preserved"), _o("upper", "disappeared_other")]
    assert classify_evidence(one_source)[0] == "evidence_sensitive"


def test_skipped_parameter_perturbation_blocks_robust():
    ch = [c for c in ALL_OK if c.check_id != "X5_perturbation"]
    assert classify_numerical(ch)[0] == "inconclusive"
    assert classify(ch, [_o("source", "preserved"), _o("lower", "preserved")]).classification == "inconclusive"


def test_limits_claim_is_reported_as_limiting_not_failed():
    ch = [
        _c("X5_perturbation", "limits_claim", "parameter_perturbation") if c.check_id == "X5_perturbation" else c
        for c in ALL_OK
    ]
    d = classify(ch, [_o("source", "preserved"), _o("lower", "preserved")])
    assert d.classification == "sensitive" and d.failed_challenge_types == []
    assert d.limiting_challenge_types == ["parameter_perturbation"]
