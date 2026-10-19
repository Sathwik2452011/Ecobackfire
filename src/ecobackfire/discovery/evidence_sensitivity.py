"""Evidence-sensitive evaluation of ONE break-even candidate (layer 4; no registry, no units library).

    candidate (root x* of g in [lo, hi])
        -> numerical falsification            (existing falsification checks X1-X4, X6)
        -> parameter perturbation             (existing X5, and optional configured robustness ranges R1)
        -> evidence scenarios                 (documented source values / lower / central / upper)
              each re-solved with the EXISTING ``find_breakevens`` through the same ``make_g``
        -> classification                     (``classification.py``; categorical rules, no percentages)

There is no second calculation engine: ``make_g(over)`` is whatever builds the model-backed decision function
(``experiments.runner.make_g``-based in production, a closed form in tests), and every scenario is solved by the
existing scan/Brent code through ``robustness._solve_case``.

A candidate that does not survive is a RESULT, never an exception. ``ConfigError`` is raised only for invalid
configuration (unknown parameter, unit mismatch, too many scenarios, ...).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict

from ecobackfire.contracts.evidence_sensitivity import (
    DIMENSION_QUESTIONS,
    INTERPRETATION,
    LIMITATIONS,
    LOST_KINDS,
    PRESERVED_KINDS,
    UNRESOLVED_KINDS,
    CandidateEvidenceEvaluation,
    ChallengeResult,
    EvidenceScenario,
    EvidenceScenarioSet,
    EvidenceSensitivityConfig,
    OutcomeKind,
    RobustnessDimension,
    ScenarioOutcome,
)
from ecobackfire.core.hashing import canonical_json
from ecobackfire.discovery.classification import challenge_type_of, classify
from ecobackfire.discovery.falsification import (
    FalsificationCheck,
    FalsificationConfig,
    FalsificationReport,
    MakeG,
    falsify_breakeven,
)
from ecobackfire.discovery.robustness import (
    PerturbationSpec,
    ReferenceRoots,
    RobustnessConfig,
    RobustnessReport,
    _solve_case,
    analyze_robustness,
)
from ecobackfire.errors import ConfigError, EcoBackfireError
from ecobackfire.experiments.breakeven import Root, find_breakevens

EVIDENCE_CHECK_RANGE = "E1_evidence_range"
EVIDENCE_CHECK_SOURCES = "E2_source_disagreement"
PARAMETER_CHECK_RANGES = "R1_parameter_ranges"

_RANGE_ROLES = ("lower", "upper")


# ------------------------------------------------------------------ scenarios


def _loss_kind(
    make_g: MakeG, over: Mapping[str, float], root: Root, lo: float, hi: float, n: int, xtol: float
) -> tuple[OutcomeKind, str]:
    """Why a scenario lost the candidate. Uses the existing scan; never raises for a failed model evaluation."""
    try:
        scan = find_breakevens(make_g(over), lo, hi, n, xtol)
    except EcoBackfireError as exc:
        return "invalid", f"{type(exc).__name__}: {exc}"
    crossings = [r for r in scan.roots if r.kind == "crossing" and r.converged]
    if any(r.beneficial_side not in (root.beneficial_side, "unknown") for r in crossings):
        return "reversed", "a crossing exists but its beneficial side is opposite to the candidate's"
    if scan.classification == "no_crossing_always_beneficial":
        return "disappeared_always_beneficial", scan.classification
    if scan.classification == "no_crossing_never_beneficial":
        return "disappeared_never_beneficial", scan.classification
    return "disappeared_other", scan.classification


def _evaluate_scenarios(
    make_g: MakeG,
    base: Mapping[str, float],
    scenarios: Sequence[EvidenceScenario],
    root: Root,
    lo: float,
    hi: float,
    n: int,
    xtol: float,
    cfg: EvidenceSensitivityConfig,
) -> list[ScenarioOutcome]:
    rcfg = RobustnessConfig()
    outcomes: list[ScenarioOutcome] = []
    for sc in scenarios:
        over = {sc.parameter: sc.value}
        case = _solve_case(make_g, over, root, lo, hi, n, xtol, rcfg, None, None, sc.parameter, sc.value,
                           base[sc.parameter], None)
        kind: OutcomeKind
        reason = case.reason
        if case.status == "preserved":
            near_edge = case.edge_margin is not None and case.edge_margin < cfg.edge_margin
            kind = "preserved_near_search_edge" if near_edge else "preserved"
            if near_edge:
                reason = (f"root within edge_margin={cfg.edge_margin} of the search window "
                          "(it may leave the window: unresolved)")
        elif case.status == "invalid":
            kind = "invalid"
        else:
            kind, why = _loss_kind(make_g, over, root, lo, hi, n, xtol)
            reason = why if kind != "invalid" else f"{case.reason}; {why}"
        outcomes.append(
            ScenarioOutcome(
                scenario_id=sc.scenario_id,
                parameter=sc.parameter,
                role=sc.role,
                evidence_id=sc.evidence_id,
                source_key=sc.source_key,
                dataset_id=sc.dataset_id,
                dataset_version=sc.dataset_version,
                value=sc.value,
                unit=sc.unit,
                outcome=kind,
                is_baseline=math.isclose(sc.value, base[sc.parameter], rel_tol=1e-12, abs_tol=0.0),
                root=case.root,
                beneficial_side=root.beneficial_side if case.status == "preserved" else None,
                root_shift_rel=case.root_shift_rel,
                edge_margin=case.edge_margin,
                n_roots=case.n_roots,
                reason=reason,
                challenge_type="evidence_range_sensitivity" if sc.role in _RANGE_ROLES else "source_disagreement",
            )
        )
    return outcomes


# ------------------------------------------------------------------ challenges


def _verdict(outcomes: Sequence[ScenarioOutcome]) -> tuple[str, list[str]]:
    lost = [o for o in outcomes if o.outcome in LOST_KINDS]
    unres = [o for o in outcomes if o.outcome in UNRESOLVED_KINDS]
    if lost:
        return "failed", [o.scenario_id for o in lost]
    if unres:
        return "inconclusive", [o.scenario_id for o in unres]
    return "passed", []


def evidence_range_check(outcomes: Sequence[ScenarioOutcome]) -> ChallengeResult:
    """E1: does the finding survive the documented lower/upper bounds of every record that documents a range?"""
    sel = [o for o in outcomes if o.challenge_type == "evidence_range_sensitivity"]
    if not sel:
        return ChallengeResult(challenge_type="evidence_range_sensitivity", check_id=EVIDENCE_CHECK_RANGE,
                               status="skipped", category="evidence_range_sensitive",
                               summary="no record documents a range for any input; none is invented")
    status, ids = _verdict(sel)
    summary = {
        "failed": f"the finding is lost at documented range bound(s): {ids}",
        "inconclusive": f"no bound loses the finding but some are unresolved: {ids}",
        "passed": f"the finding is present at all {len(sel)} documented range bound(s)",
    }[status]
    return ChallengeResult(challenge_type="evidence_range_sensitivity", check_id=EVIDENCE_CHECK_RANGE,
                           status=status, category="evidence_range_sensitive",  # type: ignore[arg-type]
                           summary=summary,
                           scenario_ids=[o.scenario_id for o in sel])


def source_disagreement_check(outcomes: Sequence[ScenarioOutcome]) -> ChallengeResult:
    """E2: does the finding survive every credible alternative record (value of each record that is not the base)?"""
    sel = [o for o in outcomes if o.challenge_type == "source_disagreement" and not o.is_baseline]
    if not sel:
        return ChallengeResult(challenge_type="source_disagreement", check_id=EVIDENCE_CHECK_SOURCES,
                               status="skipped", category="source_disagreement",
                               summary="no alternative credible record exists for any input")
    status, ids = _verdict(sel)
    summary = {
        "failed": f"the finding is lost under alternative record(s): {ids}",
        "inconclusive": f"no alternative loses the finding but some are unresolved: {ids}",
        "passed": f"the finding is present under all {len(sel)} alternative record(s)",
    }[status]
    return ChallengeResult(challenge_type="source_disagreement", check_id=EVIDENCE_CHECK_SOURCES,
                           status=status, category="source_disagreement", summary=summary,  # type: ignore[arg-type]
                           scenario_ids=[o.scenario_id for o in sel])


def _from_check(c: FalsificationCheck) -> ChallengeResult:
    return ChallengeResult(challenge_type=challenge_type_of(c.check_id), check_id=c.check_id, status=c.status,
                           category=c.category, summary=c.summary)


def _parameter_ranges_check(rep: RobustnessReport) -> ChallengeResult:
    m = rep.metrics
    lost, valid = int(m.get("n_lost", 0)), int(m.get("n_valid", 0))  # type: ignore[call-overload]
    if valid == 0:
        status, text = "inconclusive", "no configured parameter case could be evaluated"
    elif lost or rep.failed_checks:
        status = "failed"
        text = f"lost in {lost} of {valid} configured parameter cases; failed checks: {rep.failed_checks}"
    else:
        status, text = "passed", f"present in all {valid} configured parameter cases"
    return ChallengeResult(challenge_type="parameter_perturbation", check_id=PARAMETER_CHECK_RANGES,
                           status=status, category="perturbation_fragile", summary=text)  # type: ignore[arg-type]


# ------------------------------------------------------------------ dimensions


def _dimension(
    name: str, basis: str, statuses: Sequence[str], n_p: int, n_l: int, n_u: int, detail: str
) -> RobustnessDimension:
    n = n_p + n_l + n_u
    if n == 0 and not statuses:
        status = "not_assessed"
    elif n_l or "failed" in statuses or "limits_claim" in statuses:
        status = "not_held"
    elif n_u or "inconclusive" in statuses:
        status = "inconclusive"
    else:
        status = "held"
    return RobustnessDimension(dimension=name, question=DIMENSION_QUESTIONS[name],
                               status=status,  # type: ignore[arg-type]
                               n_cases=n, n_preserved=n_p, n_lost=n_l, n_unresolved=n_u, detail=detail, basis=basis)


def build_dimensions(challenges: Sequence[ChallengeResult], outcomes: Sequence[ScenarioOutcome],
                     robustness: RobustnessReport | None) -> list[RobustnessDimension]:
    by = {c.check_id: c for c in challenges}
    num_ids = ("X1_crossing_validity", "X2_numerical_stability", "X3_boundary_dependence", "X4_reproducibility",
               "X6_independent_roots")
    num = [by[i] for i in num_ids if i in by and by[i].status != "skipped"]
    n_p = sum(c.status == "passed" for c in num)
    n_l = sum(c.status in ("failed", "limits_claim") for c in num)
    n_u = sum(c.status == "inconclusive" for c in num)
    dims = [_dimension("numerical", "existing falsification checks X1-X4, X6", [c.status for c in num], n_p, n_l, n_u,
                       f"{n_p} passed, {n_l} failed/limiting, {n_u} inconclusive")]
    par = [by[i] for i in ("X5_perturbation", PARAMETER_CHECK_RANGES) if i in by and by[i].status != "skipped"]
    cases_p = cases_l = cases_u = 0
    if robustness is not None:
        cases_p = int(robustness.metrics.get("n_preserved", 0))  # type: ignore[call-overload]
        cases_l = int(robustness.metrics.get("n_lost", 0))  # type: ignore[call-overload]
        cases_u = int(robustness.metrics.get("n_invalid", 0))  # type: ignore[call-overload]
    dims.append(_dimension("parameter", "configured / user one-at-a-time perturbations (not documented evidence)",
                           [c.status for c in par], cases_p, cases_l, cases_u,
                           "; ".join(f"{c.check_id}: {c.status}" for c in par) or "not assessed"))
    ch = [o for o in outcomes if not o.is_baseline]
    dims.append(_dimension("evidence", "documented evidence ranges and alternative credible sources",
                           [], sum(o.outcome in PRESERVED_KINDS for o in ch), sum(o.outcome in LOST_KINDS for o in ch),
                           sum(o.outcome in UNRESOLVED_KINDS for o in ch),
                           f"{len(ch)} challenging scenario(s) beyond the base value"
                           if ch else "no documented evidence variation beyond the base value"))
    return dims


# ------------------------------------------------------------------ orchestration


def _validate(sets: Sequence[EvidenceScenarioSet], base: Mapping[str, float], search_var: str,
              expected_units: Mapping[str, str] | None, cfg: EvidenceSensitivityConfig) -> None:
    names = [s.parameter for s in sets]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ConfigError(f"duplicate evidence scenario set(s) for: {dup}")
    for s in sets:
        if s.parameter == search_var:
            raise ConfigError(f"{s.parameter}: the searched variable cannot be varied by evidence scenarios")
        if s.parameter not in base:
            raise ConfigError(f"{s.parameter}: not an input of the base configuration")
        for sc in s.scenarios:
            if expected_units is not None:
                if s.parameter not in expected_units:
                    raise ConfigError(f"{s.parameter}: no expected unit supplied; cannot check the unit")
                if sc.unit != expected_units[s.parameter]:
                    raise ConfigError(f"{s.parameter}: unit mismatch: scenario {sc.scenario_id} is in {sc.unit!r} "
                                      f"but the model input is {expected_units[s.parameter]!r}")
    total = sum(len(s.scenarios) for s in sets)
    if total > cfg.max_scenarios:
        raise ConfigError(f"{total} scenarios exceed max_scenarios={cfg.max_scenarios}; the evaluation is bounded")


def evaluate_candidate_evidence(
    *,
    candidate_id: str,
    model_ref: str,
    make_g: MakeG,
    base: Mapping[str, float],
    search_var: str,
    unit: str,
    root: Root,
    lo: float,
    hi: float,
    grid_points: int,
    xtol: float,
    scenario_sets: Sequence[EvidenceScenarioSet],
    config: EvidenceSensitivityConfig | None = None,
    falsification_config: FalsificationConfig | None = None,
    perturb_vars: Sequence[str] | None = None,
    robustness_perturbations: Sequence[PerturbationSpec] = (),
    robustness_config: RobustnessConfig | None = None,
    reference_roots: ReferenceRoots | None = None,
    domain: tuple[float, float] | None = None,
    expected_units: Mapping[str, str] | None = None,
) -> CandidateEvidenceEvaluation:
    """Challenge one candidate with numerical falsification, parameter perturbation and documented evidence."""
    cfg = config or EvidenceSensitivityConfig()
    sets = sorted(scenario_sets, key=lambda s: s.parameter)
    _validate(sets, base, search_var, expected_units, cfg)
    if root.beneficial_side == "unknown":
        raise ConfigError("the candidate root has no determined beneficial side to preserve")

    # 1. numerical falsification + parameter perturbation (existing machinery, unchanged)
    g = make_g({})
    fal: FalsificationReport = falsify_breakeven(
        g, root, lo, hi, grid_points=grid_points, xtol=xtol, subject=candidate_id, config=falsification_config,
        make_g=make_g, base=base, search_var=search_var, perturb_vars=perturb_vars,
        reference_roots=None if reference_roots is None else [float(v) for v in reference_roots({})], domain=domain,
    )
    challenges = [_from_check(c) for c in fal.checks]

    # 2. optional configured robustness ranges (explicitly NOT evidence)
    rob: RobustnessReport | None = None
    if robustness_perturbations:
        rob = analyze_robustness(make_g, base, search_var, root, lo, hi, grid_points=grid_points, xtol=xtol,
                                 perturbations=robustness_perturbations, config=robustness_config,
                                 reference_roots=reference_roots, expected_units=expected_units, subject=candidate_id)
        challenges.append(_parameter_ranges_check(rob))

    # 3. evidence scenarios; the whole evaluation is repeated and compared bit-for-bit
    scenarios = [sc for s in sets for sc in s.scenarios]
    runs = [_evaluate_scenarios(make_g, base, scenarios, root, lo, hi, grid_points, xtol, cfg)
            for _ in range(cfg.repeats)]
    keys = {canonical_json([o.model_dump(mode="json") for o in r]) for r in runs}
    deterministic = len(keys) == 1
    outcomes = runs[0]
    challenges.append(evidence_range_check(outcomes))
    challenges.append(source_disagreement_check(outcomes))

    decision = classify(challenges, outcomes)
    dims = build_dimensions(challenges, outcomes, rob)
    rcfg = robustness_config or RobustnessConfig()
    limitations = list(LIMITATIONS)
    no_evidence = sorted(p for p in base if p != search_var and p not in {s.parameter for s in sets if s.scenarios})
    if no_evidence:
        limitations.append(f"inputs with no evidence scenario (held at their declared values): {no_evidence}")
    for s in sets:
        if s.resolution_state == "missing":
            limitations.append(f"{s.parameter}: evidence missing ({s.resolution_decision})")
        limitations.extend(f"{s.parameter}: {w}" for w in s.warnings)
    if not deterministic:
        limitations.append("NON-DETERMINISTIC: repeated evaluation gave different scenario outcomes")
    return CandidateEvidenceEvaluation(
        candidate_id=candidate_id,
        model_ref=model_ref,
        variable_id=search_var,
        unit=unit,
        root=root.value,
        beneficial_side=root.beneficial_side,
        search_low=lo,
        search_high=hi,
        config=cfg,
        scenario_sets=list(sets),
        outcomes=outcomes,
        challenges=challenges,
        dimensions=dims,
        decision=decision,
        deterministic=deterministic,
        falsification_report_sha256=fal.sha256(),
        robustness_report_sha256=None if rob is None else rob.sha256(),
        falsification_settings=dict(fal.config),
        robustness_settings={
            "config": asdict(rcfg),
            "perturbations": [asdict(p) for p in sorted(robustness_perturbations, key=lambda p: p.name)],
        },
        limitations=limitations,
        interpretation=INTERPRETATION,
    )
