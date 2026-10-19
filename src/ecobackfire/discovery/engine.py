"""Deterministic 1-D break-even discovery over bounded parameter ranges.

For each declared parameter the engine holds every other input at its bound value, scans the decision
function g on a regular grid, refines each sign change with Brent's method (via the existing
``find_breakevens``), and turns each root into a scored Candidate. Searches that find nothing, hit
invalid inputs, or look discontinuous are recorded as RejectedCandidate with a reason, never dropped.

No randomness, no clock, no network: identical specs give identical results.
"""

from __future__ import annotations

from collections.abc import Mapping

from ecobackfire.contracts.common import QuantityValue, weakest_provenance
from ecobackfire.contracts.discovery import (
    BackfireBaseline,
    BackfireCandidateInfo,
    BackfireExclusion,
    BackfireInterval,
    BackfireParameterReport,
    BackfireReport,
    Candidate,
    DiscoveryResult,
    DiscoverySpec,
    RegionResult,
    RejectedCandidate,
    ScoreBreakdown,
)
from ecobackfire.contracts.resolution import InputEvidenceRef
from ecobackfire.core.engine import applicability_warnings, prepare_inputs
from ecobackfire.core.hashing import canonical_json, sha256_text
from ecobackfire.core.model import Model
from ecobackfire.core.trace import Trace
from ecobackfire.datasets.registry import DatasetRegistry
from ecobackfire.discovery import scoring
from ecobackfire.discovery.analyses import run_region, run_sensitivity
from ecobackfire.discovery.backfire_search import HuntConfig, HuntOutcome, HuntRoot, ParamTask, run_hunt
from ecobackfire.discovery.ranges import resolve_range
from ecobackfire.errors import ConfigError, EcoBackfireError
from ecobackfire.experiments.breakeven import Root, find_breakevens
from ecobackfire.experiments.evidence import MaterializedEvidence, materialize_bindings
from ecobackfire.experiments.runner import get_metric, make_g
from ecobackfire.models.registry import get_model

CRITERIA_NOT_YET_APPLIED = [
    "aggregation of robustness (e.g. the sensitivity analysis's share of the uncertainty box with a break-even) "
    "into each candidate's ranking score",
    "independent verification and falsification attempts (Slice D)",
    "resolution of inputs and ranges against an evidence store (Slice D)",
]
STATUS_NOTE = (
    "Every candidate is a derived numerical result of the declared model under the declared inputs. "
    "Candidates are unverified hypotheses requiring further validation; none is an empirical finding."
)


def spec_sha256(spec: DiscoverySpec) -> str:
    return sha256_text(canonical_json(spec.model_dump(mode="json")))


def _hypothesis(spec: DiscoverySpec, model: Model, opts: Mapping[str, str], var: str, root: Root, unit: str) -> str:
    side = {
        "below": "beneficial below and not beneficial above",
        "above": "not beneficial below and beneficial above",
        "unknown": "with an undetermined direction",
    }[root.beneficial_side]
    return (
        f"Within model {model.spec.ref} (system boundary {model.system_boundary(opts).id}, options {dict(opts)}), "
        f"holding all other inputs at their declared base values, decision metric "
        f"'{spec.decision_metric_ref}' changes sign at {var} = {root.value:.9g} {unit}: {side}. "
        "This is a model-derived candidate requiring further empirical validation."
    )


def _score(
    root: Root,
    base: float,
    lo: float,
    hi: float,
    swing: float,
    scale: float,
    classes: list[str],
    spec: DiscoverySpec,
    wd: float | None = None,
) -> ScoreBreakdown:
    comps = {
        "proximity": scoring.proximity_score(root.value, base, lo, hi),
        "magnitude": scoring.magnitude_score(swing, scale),
        "evidence": scoring.evidence_score(classes),
    }
    w = spec.weights
    weights = {"proximity": w.proximity, "magnitude": w.magnitude, "evidence": w.evidence}
    if wd is not None and spec.backfire is not None:
        ww = spec.backfire.well_defined_weight
        weights = {k: v * (1.0 - ww) for k, v in weights.items()}
        comps["well_defined"], weights["well_defined"] = wd, ww
        return ScoreBreakdown(
            components=comps,
            weights=weights,
            total=scoring.combine(comps, weights),
            formula=scoring.FORMULA_BACKFIRE,
        )
    return ScoreBreakdown(
        components=comps, weights=weights, total=scoring.combine(comps, weights), formula=scoring.FORMULA
    )


def _evidence_refs(
    evidence: MaterializedEvidence, required: list[str], searched: set[str]
) -> list[InputEvidenceRef]:
    """Per-input provenance for one candidate/region: the searched inputs and every input held at base."""
    if evidence.summary is None:
        return []
    rows = evidence.summary.by_variable()
    return [rows[v].ref("searched" if v in searched else "held_at_base") for v in sorted(required) if v in rows]


BACKFIRE_OBJECTIVE = (
    "Where does the environmental benefit of the intervention disappear or reverse? Objective function: the existing "
    "decision function g (sign-adjusted decision-metric output minus its threshold); g < 0 beneficial, g = 0 "
    "break-even, g > 0 adverse. No new environmental equation is introduced."
)
BACKFIRE_LIMITATIONS = [
    "Numerical search of the declared model under the declared inputs: it locates sign changes of g and cannot say "
    "whether a region is realistic; every candidate is a model-derived hypothesis.",
    "One parameter at a time with every other input held at its base value (interactions are not searched here; the "
    "existing 2-D region search is separate).",
    "Not detected: a reversal narrower than the local re-scan resolution, one between a range edge and the first "
    "grid point, a plateau hiding a dip, or sub-structure inside one refined cell. No finding is not proof of absence.",
    "Ranges are the declared ones (explicit or the binding interval); they are never widened or invented.",
]


def _g_of(metric, out: Mapping[str, float]) -> float:  # type: ignore[no-untyped-def]
    d = out[metric.output_id] - metric.threshold
    return d if metric.comparator == "lt" else -d


def _regime(g: float, scale: float, tol: float) -> str:
    return "near_break_even" if abs(g) <= tol * scale else ("beneficial" if g < 0 else "adverse")


def _baseline(model, opts, metric, base, base_out, scale, bf, spec):  # type: ignore[no-untyped-def]
    g0 = _g_of(metric, base_out)
    c0 = base_out.get("C0")
    outputs = {k: float(v) for k, v in sorted(base_out.items()) if isinstance(v, (int, float))}
    regime = _regime(g0, scale, bf.near_break_even_tol)
    return BackfireBaseline(
        metric_id=spec.decision_metric_ref,
        output_id=metric.output_id,
        comparator=metric.comparator,
        threshold=metric.threshold,
        metric_output_value=float(base_out[metric.output_id]),
        g_at_baseline=g0,
        reference_scale=scale,
        relative_change=None if not c0 else float(base_out[metric.output_id]) / float(c0),
        regime=regime,  # type: ignore[arg-type]
        regime_tolerance=bf.near_break_even_tol,
        outputs=outputs,
        inputs={
            k: QuantityValue(value=v, unit=model.spec.input_by_id(k).canonical_unit) for k, v in sorted(base.items())
        },
        chain=[
            "Baseline: the declared base inputs (every non-searched input is held here).",
            "Intervention: the model's before/after comparison at those inputs "
            "(outputs C0 -> C1 where the model defines them).",
            f"Environmental metric change: {metric.output_id} = {float(base_out[metric.output_id]):.6g} "
            f"(decision g = {g0:.6g}; regime at baseline: {regime.replace('_', ' ')}).",
            "Search region: each searched parameter's declared range, one parameter at a time.",
            "Break-even/backfire candidates: the sign changes of g found in those ranges (see candidates).",
        ],
    )


def run_discovery(
    spec: DiscoverySpec, model: Model | None = None, *, registry: DatasetRegistry | None = None
) -> DiscoveryResult:
    """Run discovery. Evidence requests in ``spec`` are resolved against ``registry`` first (unresolved or
    conflicting requests raise); the EFFECTIVE spec - requests plus resolved bindings - is what is hashed and run,
    and every candidate carries the evidence chain of every input it was computed from."""
    model = model or get_model(spec.model_ref)
    if model.spec.ref != spec.model_ref:
        raise ConfigError(f"model {model.spec.ref} does not match spec model_ref {spec.model_ref}")
    evidence = materialize_bindings(model.spec, spec.bindings, spec.evidence_requests, registry)
    if spec.evidence_requests:
        spec = spec.model_copy(update={"bindings": evidence.bindings})
    opts = model.resolve_options(spec.options)
    metric = get_metric(model, spec.decision_metric_ref)
    required = model.required_inputs(opts)
    known = {v.id for v in model.spec.inputs}
    mentioned = [p.variable_id for p in spec.parameters]
    mentioned += [a.variable_id for r in spec.regions for a in (r.x, r.y)]
    if spec.sensitivity is not None:
        mentioned += [spec.sensitivity.target.variable_id, *(p.variable_id for p in spec.sensitivity.parameters)]
    for vid in mentioned:
        if vid not in known:
            raise ConfigError(f"unknown parameter {vid!r}; model inputs are {sorted(known)}")

    prepared = prepare_inputs(model.spec, spec.bindings, required, verified_evidence=evidence.verified_evidence)
    base = prepared.values
    warnings = list(prepared.warnings)
    base_out = model.evaluate(base, opts)
    scale = max(abs(base_out.get("C0", 0.0)), abs(base_out.get("C1", 0.0)), 1.0)
    classes = [spec.bindings[v].provenance_class for v in required if not spec.bindings[v].missing]
    class_values = [c.value for c in classes]
    weakest = weakest_provenance(classes)
    assert weakest is not None  # prepare_inputs guarantees every required input is bound

    raw: list[dict[str, object]] = []
    rejected: list[RejectedCandidate] = []

    bf = spec.backfire
    hunt: HuntOutcome | None = None
    hunt_by: dict[str, object] = {}
    ranges: dict[str, tuple[float, float, object, str]] = {}
    baseline: BackfireBaseline | None = None
    if bf is not None:
        baseline = _baseline(model, opts, metric, base, base_out, scale, bf, spec)
        tasks: list[ParamTask] = []
        for pr in spec.parameters:
            if pr.variable_id in required:
                lo_, hi_, prov_, basis_ = resolve_range(spec, model, pr)
                ranges[pr.variable_id] = (lo_, hi_, prov_, basis_)
                tasks.append(
                    ParamTask(
                        pr.variable_id, make_g(model, opts, metric, base, pr.variable_id), lo_, hi_,
                        pr.grid_points, pr.xtol, scale,
                    )
                )
        if tasks:
            hunt = run_hunt(
                tasks,
                HuntConfig(
                    max_evaluations=bf.max_evaluations,
                    narrow_fraction=bf.narrow_fraction,
                    near_break_even_tol=bf.near_break_even_tol,
                    refine_points=bf.refine_points,
                    touch_tol=bf.touch_tol,
                    max_local_points=bf.max_local_grid_points,
                ),
            )
            hunt_by = {ph.task.var: ph for ph in hunt.params}
    n_from_refinement = 0
    param_reports: dict[str, BackfireParameterReport] = {}

    for pr in spec.parameters:
        var = pr.variable_id
        if var not in required:
            rejected.append(
                RejectedCandidate(
                    variable_id=var,
                    code="parameter_not_used_under_options",
                    detail=f"{var} is not a required input under options {dict(opts)}, so it cannot change the result",
                )
            )
            continue
        if bf is not None:
            lo, hi, range_prov, basis = ranges[var]  # type: ignore[assignment]
        else:
            lo, hi, range_prov, basis = resolve_range(spec, model, pr)
        ph = hunt_by.get(var) if bf is not None else None
        if ph is not None and ph.status != "searched":  # type: ignore[attr-defined]
            code = "budget_exhausted" if ph.status == "budget_exhausted" else "evaluation_failed"  # type: ignore[attr-defined]
            rejected.append(
                RejectedCandidate(
                    variable_id=var, code=code, detail=ph.note, search_low=lo, search_high=hi  # type: ignore[arg-type,attr-defined]
                )
            )
            continue
        if ph is not None:
            scan = ph.scan  # type: ignore[attr-defined]
        else:
            try:
                scan = find_breakevens(make_g(model, opts, metric, base, var), lo, hi, pr.grid_points, pr.xtol)
            except EcoBackfireError as exc:
                rejected.append(
                    RejectedCandidate(
                        variable_id=var,
                        code="evaluation_failed",
                        detail=f"{type(exc).__name__}: {exc}",
                        search_low=lo,
                        search_high=hi,
                    )
                )
                continue

        unit = model.spec.input_by_id(var).canonical_unit
        range_warnings = [
            f"search range ({label} end) {w}"
            for label, x in (("low", lo), ("high", hi))
            for w in applicability_warnings(model.spec, {var: x})
        ]
        if ph is not None:
            items: list[tuple[int, Root, HuntRoot | None]] = [(h.k, h.root, h) for h in ph.roots]  # type: ignore[attr-defined]
            for r in ph.rejected_grid_roots:  # type: ignore[attr-defined]
                is_disc = r.kind == "discontinuity_suspected"
                rejected.append(
                    RejectedCandidate(
                        variable_id=var,
                        code="discontinuity_suspected" if is_disc else "root_not_converged",
                        detail=f"|g|={abs(r.residual):.3g} at the refined root; not a continuous crossing",
                        search_low=lo,
                        search_high=hi,
                        value=r.value,
                    )
                )
            for t in ph.touches:  # type: ignore[attr-defined]
                rejected.append(
                    RejectedCandidate(
                        variable_id=var,
                        code="tangent_touch_no_reversal",
                        detail=(
                            f"g reaches zero (|g|={abs(t.g):.3g}) at {t.x:.9g} without changing sign ({t.origin}): the "
                            "benefit reaches break-even but does not reverse; a tangent touch is numerically fragile"
                        ),
                        search_low=lo,
                        search_high=hi,
                        value=t.x,
                    )
                )
            skip_generic = bool(ph.touches or ph.rejected_grid_roots)  # type: ignore[attr-defined]
        else:
            items = [(k, r, None) for k, r in enumerate(scan.roots)]
            skip_generic = False
        if ph is not None:
            param_reports[var] = BackfireParameterReport(
                variable_id=var,
                unit=unit,
                low=lo,
                high=hi,
                grid_points=pr.grid_points,
                range_basis=basis,
                status="searched",
                behaviour=ph.behaviour,  # type: ignore[attr-defined,arg-type]
                evaluations=ph.evaluations,  # type: ignore[attr-defined]
                n_crossings_grid=sum(1 for h in ph.roots if h.origin == "global_grid"),  # type: ignore[attr-defined]
                n_crossings_refined=sum(1 for h in ph.roots if h.origin == "local_refinement"),  # type: ignore[attr-defined]
                n_touches=len(ph.touches),  # type: ignore[attr-defined]
                n_extrema_refined=ph.n_extrema_refined,  # type: ignore[attr-defined]
                n_extrema_unrefined=ph.n_extrema_unrefined,  # type: ignore[attr-defined]
                min_abs_g_over_scale=None if ph.min_abs_g is None else ph.min_abs_g / scale,  # type: ignore[attr-defined]
                intervals=[],
                note=ph.note,  # type: ignore[attr-defined]
            )
        if not items:
            if not skip_generic:
                code = {
                    "no_crossing_always_beneficial": "no_sign_change_always_beneficial",
                    "no_crossing_never_beneficial": "no_sign_change_never_beneficial",
                }.get(scan.classification, "no_sign_change_indeterminate")
                rejected.append(
                    RejectedCandidate(
                        variable_id=var,
                        code=code,  # type: ignore[arg-type]
                        detail=scan.note,
                        search_low=lo,
                        search_high=hi,
                    )
                )
            continue

        gvals = [g for _, g in scan.scan]
        swing = max(gvals) - min(gvals)
        pbounds = model.spec.input_by_id(var).physical_bounds
        for k, root, hr in items:
            if hr is None and (root.kind == "discontinuity_suspected" or not root.converged):
                is_disc = root.kind == "discontinuity_suspected"
                reject_code = "discontinuity_suspected" if is_disc else "root_not_converged"
                rejected.append(
                    RejectedCandidate(
                        variable_id=var,
                        code=reject_code,  # type: ignore[arg-type]
                        detail=f"|g|={abs(root.residual):.3g} at the refined root; not a continuous crossing",
                        search_low=lo,
                        search_high=hi,
                        value=root.value,
                    )
                )
                continue
            vals = dict(base)
            vals[var] = root.value
            tr = Trace()
            model.evaluate(vals, opts, trace=tr)
            wd: float | None = None
            win = (lo, hi, pr.grid_points)
            cwarn = list(range_warnings)
            if hr is not None:
                win = hr.window
                inb = pbounds is None or (
                    (pbounds.min is None or root.value >= pbounds.min)
                    and (pbounds.max is None or root.value <= pbounds.max)
                )
                wdc = scoring.well_defined_components(
                    converged=root.converged, residual=root.residual, g_low=root.g_low, g_high=root.g_high,
                    scale=scale, root=root.value, lo=lo, hi=hi, within_physical_bounds=inb,
                )
                wd = wdc["well_defined"]
                if hr.origin == "local_refinement":
                    n_from_refinement += 1
                    cwarn.append(
                        "found by sub-grid refinement of a suspect cell pair (a reversal region narrower than the "
                        f"global grid spacing); its search window is local: [{win[0]:.9g}, {win[1]:.9g}], "
                        f"{win[2]} points"
                    )
                    if hr.resolution_limited:
                        cwarn.append(
                            "the local window could not reach the target resolution (max_local_grid_points); "
                            "downstream re-solves may not resolve this narrow region"
                        )
            raw.append(
                {
                    "var": var,
                    "k": k,
                    "root": root,
                    "lo": win[0],
                    "hi": win[1],
                    "plo": lo,
                    "phi": hi,
                    "unit": unit,
                    "grid": win[2],
                    "pgrid": pr.grid_points,
                    "basis": basis,
                    "prov": range_prov,
                    "warnings": cwarn,
                    "trace": tr.steps,
                    "hr": hr,
                    "score": _score(root, base[var], lo, hi, swing, scale, class_values, spec, wd),
                }
            )

    region_results: list[RegionResult] = []
    for rs in spec.regions:
        out = run_region(spec, model, opts, metric, base, required, rs)
        if isinstance(out, RegionResult):
            refs = _evidence_refs(evidence, required, {rs.x.variable_id, rs.y.variable_id})
            region_results.append(out.model_copy(update={"input_evidence": refs}) if refs else out)
        else:
            rejected.append(out)
    sensitivity = (
        run_sensitivity(spec, model, opts, metric, base, required, spec.sensitivity)
        if spec.sensitivity is not None
        else None
    )
    if sensitivity is not None and evidence.summary is not None:
        s_vars = {spec.sensitivity.target.variable_id, *(p.variable_id for p in spec.sensitivity.parameters)}  # type: ignore[union-attr]
        sensitivity = sensitivity.model_copy(update={"input_evidence": _evidence_refs(evidence, required, s_vars)})

    raw.sort(key=lambda d: (-d["score"].total, d["var"], d["root"].value))  # type: ignore[attr-defined]
    candidates: list[Candidate] = []
    for rank, d in enumerate(raw, start=1):
        root: Root = d["root"]  # type: ignore[assignment]
        var = str(d["var"])
        unit = str(d["unit"])
        info: BackfireCandidateInfo | None = None
        if bf is not None and baseline is not None:
            hr = d["hr"]
            plo, phi_, pgrid = float(d["plo"]), float(d["phi"]), int(d["pgrid"])  # type: ignore[arg-type]
            bv = base[var]
            side = root.beneficial_side
            info = BackfireCandidateInfo(
                origin=hr.origin if hr is not None else "global_grid",  # type: ignore[attr-defined]
                model_ref=model.spec.ref,
                objective=BACKFIRE_OBJECTIVE,
                baseline_metric_output=baseline.metric_output_value,
                baseline_g=baseline.g_at_baseline,
                baseline_regime=baseline.regime,
                metric_before=root.g_low,
                metric_after=root.g_high,
                transition={
                    "below": f"beneficial below, adverse above {var} = {root.value:.9g} {unit}",
                    "above": f"adverse below, beneficial above {var} = {root.value:.9g} {unit}",
                    "unknown": f"direction undetermined at {var} = {root.value:.9g} {unit}",
                }[side],
                estimated_breakeven=root.value,
                estimate_uncertainty=None
                if root.bracket_low is None or root.bracket_high is None
                else root.bracket_high - root.bracket_low,
                distance_from_baseline=root.value - bv,
                distance_relative_to_baseline=None if bv == 0 else (root.value - bv) / abs(bv),
                distance_range_normalized=abs(root.value - bv) / (phi_ - plo),
                baseline_on_beneficial_side=None
                if side == "unknown" or bv == root.value
                else ((bv < root.value) == (side == "below")),
                parameter_range=(plo, phi_, pgrid),
                assumptions={
                    v: {
                        "value": base[v],
                        "unit": model.spec.input_by_id(v).canonical_unit,
                        "provenance_class": spec.bindings[v].provenance_class.value,
                    }
                    for v in sorted(required)
                    if v != var and v in spec.bindings
                },
                search_configuration={
                    "method": "global grid scan + Brent; sub-grid refinement of suspect extrema",
                    "window": [d["lo"], d["hi"], d["grid"]],
                    "max_evaluations": bf.max_evaluations,
                    "evaluations_performed": hunt.evaluations if hunt is not None else 0,
                    "other_inputs": "held at base",
                    "xtol": next(p.xtol for p in spec.parameters if p.variable_id == var),
                    "score_policy": scoring.WELL_DEFINED_POLICY,
                },
            )
        candidates.append(
            Candidate(
                candidate_id=f"{var}#{d['k']}",
                rank=rank,
                variable_id=var,
                unit=unit,
                value=root.value,
                root_kind=root.kind,  # type: ignore[arg-type]
                bracket_low=root.bracket_low,
                bracket_high=root.bracket_high,
                g_low=root.g_low,
                g_high=root.g_high,
                residual=root.residual,
                iterations=root.iterations,
                beneficial_side=root.beneficial_side,
                search_low=float(d["lo"]),  # type: ignore[arg-type]
                search_high=float(d["hi"]),  # type: ignore[arg-type]
                grid_points=int(d["grid"]),  # type: ignore[arg-type]
                base_value=base[var],
                range_basis=str(d["basis"]),
                range_provenance=d["prov"],  # type: ignore[arg-type]
                weakest_input_provenance=weakest,
                hypothesis=_hypothesis(spec, model, opts, var, root, unit),
                score=d["score"],  # type: ignore[arg-type]
                warnings=list(d["warnings"]),  # type: ignore[arg-type]
                trace_at_root=d["trace"],  # type: ignore[arg-type]
                input_evidence=_evidence_refs(evidence, required, {var}),
                backfire=info,
            )
        )

    backfire_report: BackfireReport | None = None
    if bf is not None and baseline is not None:
        n_adverse = n_narrow = 0
        for pr in spec.parameters:
            var = pr.variable_id
            ph = hunt_by.get(var)
            if var not in param_reports:
                if ph is not None and ph.status != "searched":  # type: ignore[attr-defined]
                    lo_, hi_, _pv, basis_ = ranges[var]
                    param_reports[var] = BackfireParameterReport(
                        variable_id=var, unit=model.spec.input_by_id(var).canonical_unit, low=lo_, high=hi_,
                        grid_points=pr.grid_points, range_basis=basis_,
                        status="budget_exhausted" if ph.status == "budget_exhausted" else "failed",  # type: ignore[attr-defined]
                        behaviour="not_searched", evaluations=ph.evaluations,  # type: ignore[attr-defined]
                        n_crossings_grid=0, n_crossings_refined=0, n_touches=0, n_extrema_refined=0,
                        n_extrema_unrefined=0, min_abs_g_over_scale=None, intervals=[], note=ph.note,  # type: ignore[attr-defined]
                    )
                else:
                    param_reports[var] = BackfireParameterReport(
                        variable_id=var, unit=model.spec.input_by_id(var).canonical_unit, low=None, high=None,
                        grid_points=pr.grid_points, range_basis=pr.basis, status="excluded",
                        behaviour="not_searched", evaluations=0, n_crossings_grid=0, n_crossings_refined=0,
                        n_touches=0, n_extrema_refined=0, n_extrema_unrefined=0, min_abs_g_over_scale=None,
                        intervals=[], note="not a required input under the model options",
                    )
                continue
            assert ph is not None
            lo_, hi_ = ranges[var][0], ranges[var][1]
            bval = base[var]

            def label(kind: str, k: int | None, _v: str = var) -> str:
                if kind == "candidate":
                    return f"{_v}#{k}"
                return "range_edge" if kind == "range_edge" else "rejected_boundary"

            ivs = []
            for iv in ph.intervals:  # type: ignore[attr-defined]
                frac = (iv.high - iv.low) / (hi_ - lo_)
                ivs.append(
                    BackfireInterval(
                        low=iv.low, high=iv.high, regime=iv.regime, width_fraction=frac,
                        narrow=frac < bf.narrow_fraction, contains_baseline=iv.low <= bval <= iv.high,
                        lower_boundary=label(iv.lower_kind, iv.lower_k),
                        upper_boundary=label(iv.upper_kind, iv.upper_k),
                    )
                )
                bounded = iv.lower_kind != "range_edge" or iv.upper_kind != "range_edge"
                if iv.regime == "adverse" and bounded:
                    n_adverse += 1
                    n_narrow += int(frac < bf.narrow_fraction)
            param_reports[var] = param_reports[var].model_copy(update={"intervals": ivs})
        searched = [r for r in param_reports.values() if r.status == "searched"]
        excl = [
            BackfireExclusion(variable_id=r.variable_id, reason=r.note or r.status)
            for r in param_reports.values()
            if r.status != "searched"
        ]
        report_status = (
            "no_searchable_parameters"
            if hunt is None
            else ("budget_exhausted" if hunt.status == "budget_exhausted" else "complete")
        )
        backfire_report = BackfireReport(
            status=report_status,  # type: ignore[arg-type]
            objective=BACKFIRE_OBJECTIVE,
            baseline=baseline,
            max_evaluations=bf.max_evaluations,
            evaluations_performed=0 if hunt is None else hunt.evaluations,
            parameters=[param_reports[p.variable_id] for p in spec.parameters],
            excluded=excl,
            n_parameters_searched=len(searched),
            n_candidates_found=len(candidates),
            n_candidates_from_refinement=n_from_refinement,
            n_rejected=len(rejected),
            n_reversal_regions=n_adverse,
            n_narrow_reversal_regions=n_narrow,
            policy={
                "narrow_fraction": bf.narrow_fraction,
                "near_break_even_tol": bf.near_break_even_tol,
                "refine_points": bf.refine_points,
                "touch_tol": bf.touch_tol,
                "well_defined_weight": bf.well_defined_weight,
                "max_local_grid_points": bf.max_local_grid_points,
                "well_defined_policy": scoring.WELL_DEFINED_POLICY,
                "evaluation_counting": "evaluations of the decision function g during the search (scan, Brent, "
                "refinement); the baseline evaluation and one diagnostic trace evaluation per candidate are not "
                "counted here (the mission's budget usage counts every model evaluation)",
            },
            limitations=list(BACKFIRE_LIMITATIONS),
        )

    unused = sorted(set(spec.bindings) - set(required))
    if unused:
        warnings.append(f"bound but unused under the chosen options: {unused}")
    if evidence.summary is not None and evidence.summary.inputs_without_evidence:
        warnings.append(
            "inputs without evidence (assumed or illustrative); every candidate is conditional on them: "
            f"{evidence.summary.inputs_without_evidence}"
        )
    return DiscoveryResult(
        discovery_id=spec.discovery_id,
        spec_sha256=spec_sha256(spec),
        model_ref=model.spec.ref,
        options=opts,
        system_boundary=model.system_boundary(opts),
        functional_unit=model.spec.assumptions.functional_unit,
        decision_metric_ref=spec.decision_metric_ref,
        base_inputs={
            k: QuantityValue(value=v, unit=model.spec.input_by_id(k).canonical_unit) for k, v in sorted(base.items())
        },
        weakest_input_provenance=weakest,
        candidates=candidates,
        regions=region_results,
        sensitivity=sensitivity,
        rejected=rejected,
        scoring_policy={
            "formula": scoring.FORMULA if bf is None else scoring.FORMULA_BACKFIRE,
            "weights": spec.weights.model_dump(mode="json"),
            "evidence_weight_by_provenance_class": scoring.EVIDENCE_WEIGHT,
            "reference_scale_kgCO2e_per_yr": scale,
        },
        criteria_not_yet_applied=list(CRITERIA_NOT_YET_APPLIED),
        status_note=STATUS_NOTE,
        warnings=warnings,
        evidence_resolution=evidence.summary,
        backfire=backfire_report,
    )
