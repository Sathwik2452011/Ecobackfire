"""Automated backfire hunt at mission level: baseline, auto parameter selection, candidate contract, falsification /
robustness / evidence propagation, budget, determinism, and the E2 regression fixture. Real registry for the canonical
mission; SYNTHETIC registry (not environmental data) for the evidence-range variant."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from _resolution_fixtures import build_registry, f_record
from test_mission import _synthetic_mission

from ecobackfire import api
from ecobackfire.contracts.mission import ResearchMission
from ecobackfire.core.hashing import sha256_bytes
from ecobackfire.datasets.registry import DatasetRegistry
from ecobackfire.discovery.engine import run_discovery
from ecobackfire.mission import runner as mr
from ecobackfire.mission.loader import load_mission
from ecobackfire.mission.planner import plan_mission
from ecobackfire.verification.reference import rebound_analytical as ana

pytestmark = pytest.mark.software

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "missions" / "example.toml"
E2 = ROOT / "examples" / "rebound_breakeven" / "e2_breakeven_eps.toml"
REAL = ROOT / "data" / "releases"


@pytest.fixture(scope="module")
def reg() -> DatasetRegistry:
    return DatasetRegistry.from_directory(REAL)


@pytest.fixture(scope="module")
def run(reg: DatasetRegistry, tmp_path_factory: pytest.TempPathFactory) -> mr.MissionRun:
    return mr.run_mission(load_mission(EXAMPLE), tmp_path_factory.mktemp("bf"), reg, source_path=str(EXAMPLE))


def mdict() -> dict[str, Any]:
    return tomllib.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_canonical_mission_declares_the_hunt_without_naming_the_answer() -> None:
    m = load_mission(EXAMPLE)
    assert m.backfire is not None and m.backfire.max_evaluations > 0
    # the mission supplies ranges only; no break-even value appears anywhere in it
    forbidden = {"value", "breakeven", "estimated_breakeven", "root", "answer"}
    assert all(not (set(p.model_dump()) & forbidden) for p in m.search)


def test_baseline_is_defined_and_matches_the_model(run: mr.MissionRun) -> None:
    b = run.result.backfire
    assert b is not None
    base = b.baseline
    assert base.regime == "beneficial" and base.g_at_baseline < 0
    assert base.metric_output_value == pytest.approx(base.outputs["dC"])  # no new equation: the model's own output
    assert base.relative_change == pytest.approx(base.outputs["dC"] / base.outputs["C0"])
    assert base.chain[0].startswith("Baseline") and len(base.chain) >= 4


def test_parameters_are_selected_automatically_with_reasons(run: mr.MissionRun) -> None:
    plan = run.result.plan
    assert plan is not None and plan.backfire is not None
    src = {p.variable_id: p.source for p in plan.backfire.participating}
    assert src["epsilon"] == "mission_search"
    auto = {k for k, v in src.items() if v == "auto_binding_interval"}
    assert auto >= {"E0", "EM0", "eta", "phi"}  # not listed in [[search]]; found via documented intervals
    assert {e.variable_id for e in plan.backfire.excluded} == {"s_max"}
    assert all(e.reason for e in plan.backfire.excluded)
    # declared bounds respected: auto ranges are exactly the binding's documented interval
    b = load_mission(EXAMPLE).bindings["eta"].uncertainty
    eta = next(p for p in plan.backfire.participating if p.variable_id == "eta")
    assert (eta.low, eta.high) == (b.low, b.high)  # type: ignore[union-attr]


def test_discovery_finds_breakeven_automatically_and_matches_closed_form(run: mr.MissionRun) -> None:
    c = run.result.candidates[0].candidate
    inputs = {k: b.value for k, b in load_mission(EXAMPLE).bindings.items()}
    inputs["f"] = 0.7054
    want = ana.predicted_epsilon_roots(inputs, dict(load_mission(EXAMPLE).options))[0]
    assert c.variable_id == "epsilon" and c.value == pytest.approx(want, rel=1e-9)


def test_candidate_uses_existing_contract_and_is_interpretable_against_baseline(run: mr.MissionRun) -> None:
    for rc in run.result.candidates:
        c = rc.candidate
        info = c.backfire
        assert info is not None  # same Candidate contract, extended; no competing format
        assert info.model_ref == "efficiency_rebound@1.0.0" and info.assumptions and info.search_configuration
        assert info.estimated_breakeven == c.value
        assert info.distance_from_baseline == pytest.approx(c.value - c.base_value)
        assert info.baseline_regime == "beneficial"
        assert info.metric_before is not None and info.metric_after is not None
        assert info.metric_before * info.metric_after < 0  # a sign change
        assert info.parameter_range[0] <= c.value <= info.parameter_range[1]  # within declared bounds


def test_multiple_candidates_found_and_ranked_transparently(run: mr.MissionRun) -> None:
    cands = run.result.candidates
    assert len(cands) >= 2 and {c.candidate.variable_id for c in cands} >= {"epsilon", "f"}
    assert [c.rank for c in cands] == list(range(1, len(cands) + 1))
    for rc in cands:
        assert rc.candidate.score.formula and abs(sum(rc.candidate.score.weights.values()) - 1.0) < 1e-9
        assert 0.0 <= rc.ranking_score <= 1.0


def test_boundary_detection_reports_regimes_per_parameter(run: mr.MissionRun) -> None:
    b = run.result.backfire
    assert b is not None
    by = {r.variable_id: r for r in b.regions}
    assert by["epsilon"].behaviour == "reverses" and by["epsilon"].n_reversal_intervals == 1
    assert by["eta"].behaviour == "remains_beneficial"
    assert by["eta"].source == "auto_binding_interval" and by["eta"].evidence_constraint


def test_every_candidate_is_falsified_and_robustness_tested(run: mr.MissionRun) -> None:
    r = run.result
    ids = {c.candidate.candidate_id for c in r.candidates}
    assert {v.candidate_id for v in r.falsification} == ids  # candidate -> falsification propagation
    assert {v.candidate_id for v in r.robustness} == ids  # candidate -> robustness propagation
    assert {v.candidate_id for v in r.classification} == ids
    assert all(v.status == "assessed" for v in r.falsification)
    assert all(rc.evaluated for rc in r.candidates)


def test_summary_answers_the_mission_questions(run: mr.MissionRun) -> None:
    b = run.result.backfire
    assert b is not None and b.status == "complete"
    assert b.evaluations_performed <= b.max_evaluations and b.evaluations_performed > 0
    assert b.candidates_found == len(b.outcomes) == 2 and b.searches_rejected >= 1
    assert set(b.survived_falsification) | set(b.candidates_falsified) == {o.candidate_id for o in b.outcomes}
    assert b.strongest_finding is not None and "not an empirical finding" in b.strongest_finding_statement
    assert b.parameters_searched and b.regions and b.baseline


def test_eliminated_candidates_are_kept_with_reasons() -> None:
    from ecobackfire.contracts.mission import BackfireOutcome  # noqa: F401
    from ecobackfire.mission.backfire import fate_of

    assert fate_of("refuted") == fate_of("boundary_dependent") == "eliminated"
    assert fate_of("robust") == "survived" and fate_of("evidence_sensitive") == "sensitive"
    assert fate_of("evidence_insufficient") == "survived_limited" and fate_of(None) == "not_assessed"


def test_evidence_range_makes_candidate_evidence_assessed(tmp_path: Path) -> None:
    rg = build_registry(tmp_path / "r", [f_record(uncertainty={"kind": "interval", "low": 600.0, "high": 800.0})])
    d = _synthetic_mission()
    d["backfire"] = {"max_evaluations": 20000}
    r = mr.run_mission(ResearchMission.model_validate(d), tmp_path / "o", rg).result
    assert r.backfire is not None
    top = next(o for o in r.backfire.outcomes if o.variable_id == "epsilon")
    assert top.evidence_class not in (None, "evidence_insufficient")
    assert top.candidate_id in set(r.backfire.evidence_robust) | set(r.backfire.evidence_sensitive)


def test_budget_exhaustion_is_a_valid_partial_result(reg: DatasetRegistry, tmp_path: Path) -> None:
    d = mdict()
    d["backfire"] = {"max_evaluations": 500, "default_grid_points": 201}
    m = ResearchMission.model_validate(d)
    disc = run_discovery(plan_mission(m, reg).spec, plan_mission(m, reg).model, registry=reg)  # type: ignore[arg-type]
    assert disc.backfire is not None and disc.backfire.status == "budget_exhausted"
    assert disc.backfire.evaluations_performed <= 500
    assert any(x.code == "budget_exhausted" for x in disc.rejected)  # unsearched parameters are recorded
    res = mr.run_mission(m, tmp_path, reg).result
    assert res.backfire is not None and res.backfire.status == "budget_exhausted"
    assert "BUDGET WAS EXHAUSTED" in res.backfire.strongest_finding_statement


def test_discovery_is_deterministic(reg: DatasetRegistry) -> None:
    m = load_mission(EXAMPLE)
    po = plan_mission(m, reg)
    a = run_discovery(po.spec, po.model, registry=reg)  # type: ignore[arg-type]
    b = run_discovery(po.spec, po.model, registry=reg)  # type: ignore[arg-type]
    assert a.model_dump_json() == b.model_dump_json()


def test_mission_without_backfire_is_unchanged(reg: DatasetRegistry, tmp_path: Path) -> None:
    d = mdict()
    del d["backfire"]
    r = mr.run_mission(ResearchMission.model_validate(d), tmp_path, reg).result
    assert r.backfire is None and "backfire" not in json.loads(r.model_dump_json())
    assert all(c.candidate.backfire is None for c in r.candidates)


def test_e2_regression_fixture_is_unchanged_and_agrees_with_closed_form() -> None:
    assert sha256_bytes(E2.read_bytes()) == _E2_SHA  # the fixture file itself is untouched
    cfg = api.load_config(E2)
    root = api.run_experiment(cfg).breakeven.roots[0].value  # type: ignore[union-attr]
    inputs = {k: b.value for k, b in cfg.bindings.items()}
    assert root == pytest.approx(ana.predicted_epsilon_roots(inputs, dict(cfg.options))[0], rel=1e-9)
    assert root == pytest.approx(1.11275746800796, rel=1e-12)  # the value pinned by the existing expected outputs


_E2_SHA = "39da79335f14a38f6f4d4175ba2406e28b6157f6a19bca617b231ea983f5a35d"
