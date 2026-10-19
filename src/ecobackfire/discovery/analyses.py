"""Glue between the pure numerics (regions.py, sampling.py) and the model/contracts: 2-D reversal-region
search and seeded sensitivity screening. Deterministic; no clock, no network.
"""

from __future__ import annotations

import importlib.metadata as md
from collections.abc import Callable, Mapping

import numpy as np

from ecobackfire.contracts.discovery import (
    DiscoverySpec,
    RegionResult,
    RegionSearch,
    RejectedCandidate,
    SensitivityResult,
    SensitivityRow,
    SensitivitySpec,
)
from ecobackfire.contracts.model import DecisionMetric
from ecobackfire.core.engine import applicability_warnings
from ecobackfire.core.model import Model
from ecobackfire.discovery import regions, sampling
from ecobackfire.discovery.ranges import resolve_range
from ecobackfire.errors import ConfigError, EcoBackfireError
from ecobackfire.experiments.breakeven import find_breakevens
from ecobackfire.experiments.runner import make_g

SENSITIVITY_METHOD = (
    "scrambled Sobol quasi-Monte-Carlo sample + Spearman rank correlation with the break-even location "
    "(screening only)"
)
SENSITIVITY_LIMITATIONS = [
    "Rank correlation detects monotone influence only. An input whose effect changes direction across the "
    "sampled box (for example inputs acting through the sign of EM1 - EM0) can show a near-zero value despite "
    "a real effect.",
    "It is not a variance-based (Sobol) index and says nothing about interactions.",
    "Samples are uniform over the declared ranges; those ranges are the user's, not evidence-based, unless "
    "stated otherwise.",
    "The QMC stream is reproducible for a given scipy version; the version is recorded.",
]


def _decision_fn2(
    model: Model, opts: Mapping[str, str], metric: DecisionMetric, base: Mapping[str, float], xv: str, yv: str
) -> Callable[[float, float], float]:
    def g(x: float, y: float) -> float:
        values = dict(base)
        values[xv] = x
        values[yv] = y
        d = model.evaluate(values, opts)[metric.output_id] - metric.threshold
        return d if metric.comparator == "lt" else -d

    return g


def run_region(
    spec: DiscoverySpec,
    model: Model,
    opts: Mapping[str, str],
    metric: DecisionMetric,
    base: Mapping[str, float],
    required: list[str],
    rs: RegionSearch,
) -> RegionResult | RejectedCandidate:
    xv, yv = rs.x.variable_id, rs.y.variable_id
    label = f"{xv}|{yv}"
    for v in (xv, yv):
        if v not in required:
            return RejectedCandidate(
                variable_id=label,
                code="parameter_not_used_under_options",
                detail=f"{v} is not a required input under options {dict(opts)}",
            )
    xlo, xhi, _, xbasis = resolve_range(spec, model, rs.x)
    ylo, yhi, _, ybasis = resolve_range(spec, model, rs.y)
    try:
        sc = regions.scan_region(
            _decision_fn2(model, opts, metric, base, xv, yv),
            xlo,
            xhi,
            rs.x.grid_points,
            ylo,
            yhi,
            rs.y.grid_points,
            min(rs.x.xtol, rs.y.xtol),
        )
    except EcoBackfireError as exc:
        return RejectedCandidate(variable_id=label, code="evaluation_failed", detail=f"{type(exc).__name__}: {exc}")
    cls = sc.classification
    if cls != "reversal_region":
        code = f"no_reversal_in_region_{cls}"
        return RejectedCandidate(
            variable_id=label,
            code=code,  # type: ignore[arg-type]
            detail=(
                f"over the scanned region the decision metric never changes sign ({sc.n_beneficial} beneficial, "
                f"{sc.n_not_beneficial} not beneficial, {sc.n_zero} zero of {sc.n_cells} grid cells)"
            ),
        )
    bx, by = base[xv], base[yv]
    gb = _decision_fn2(model, opts, metric, base, xv, yv)(bx, by)
    base_dec = "beneficial" if gb < 0 else "not_beneficial" if gb > 0 else "indeterminate"
    warnings = [
        f"{name} range ({end} end) {w}"
        for name, lo, hi in ((xv, xlo, xhi), (yv, ylo, yhi))
        for end, val in (("low", lo), ("high", hi))
        for w in applicability_warnings(model.spec, {name: val})
    ]
    if sc.suspected_discontinuities:
        n_susp = len(sc.suspected_discontinuities)
        warnings.append(f"{n_susp} suspected discontinuity point(s) excluded from the boundary")
    if not sc.boundary:
        warnings.append("both decision signs occur but no boundary point was resolved (sub-grid structure?)")
    dist = regions.min_normalized_distance(sc.boundary, bx, by, xlo, xhi, ylo, yhi)
    return RegionResult(
        x_variable_id=xv,
        y_variable_id=yv,
        x_unit=model.spec.input_by_id(xv).canonical_unit,
        y_unit=model.spec.input_by_id(yv).canonical_unit,
        x_low=xlo,
        x_high=xhi,
        y_low=ylo,
        y_high=yhi,
        nx=sc.nx,
        ny=sc.ny,
        n_beneficial=sc.n_beneficial,
        n_not_beneficial=sc.n_not_beneficial,
        n_zero=sc.n_zero,
        fraction_not_beneficial=sc.n_not_beneficial / sc.n_cells,
        boundary_points=sc.boundary,
        n_suspected_discontinuities=len(sc.suspected_discontinuities),
        base_x=bx,
        base_y=by,
        base_inside_ranges=xlo <= bx <= xhi and ylo <= by <= yhi,
        base_decision=base_dec,  # type: ignore[arg-type]
        base_to_boundary_distance=dist,
        x_range_basis=xbasis,
        y_range_basis=ybasis,
        hypothesis=(
            f"Within model {model.spec.ref}, holding all other inputs at their declared base values, the decision "
            f"metric '{spec.decision_metric_ref}' reverses across a boundary in the ({xv}, {yv}) plane: "
            f"{sc.n_not_beneficial} of {sc.n_cells} scanned grid cells are not beneficial. This is a model-derived "
            "candidate requiring further empirical validation."
        ),
        warnings=warnings,
    )


def run_sensitivity(
    spec: DiscoverySpec,
    model: Model,
    opts: Mapping[str, str],
    metric: DecisionMetric,
    base: Mapping[str, float],
    required: list[str],
    ss: SensitivitySpec,
) -> SensitivityResult:
    tv = ss.target.variable_id
    if tv not in required:
        raise ConfigError(f"sensitivity target {tv!r} is not a required input under options {dict(opts)}")
    tlo, thi, _, _ = resolve_range(spec, model, ss.target)
    names: list[str] = []
    lows: list[float] = []
    highs: list[float] = []
    skipped: list[str] = []
    for pr in ss.parameters:
        if pr.variable_id not in required:
            skipped.append(f"{pr.variable_id}: not a required input under the chosen options")
            continue
        lo, hi, _, _ = resolve_range(spec, model, pr)
        names.append(pr.variable_id)
        lows.append(lo)
        highs.append(hi)
    if not names:
        raise ConfigError("sensitivity has no sampled parameter that the model uses under the chosen options")

    def target(point: np.ndarray) -> float | None:
        vals = dict(base)
        vals.update({n: float(v) for n, v in zip(names, point, strict=True)})
        sc = find_breakevens(make_g(model, opts, metric, vals, tv), tlo, thi, ss.target.grid_points, ss.target.xtol)
        ok = [r.value for r in sc.roots if r.kind in ("crossing", "grid_zero") and r.converged]
        return ok[0] if ok else None

    res = sampling.sample_function(target, lows, highs, ss.n_samples_log2, ss.seed)
    rho = sampling.rank_correlations(res.points, res.values)
    order = sorted(
        range(len(names)), key=lambda i: (rho[i] is None, -(abs(rho[i]) if rho[i] is not None else 0.0), names[i])
    )
    rows = [
        SensitivityRow(
            rank=k,
            variable_id=names[i],
            unit=model.spec.input_by_id(names[i]).canonical_unit,
            low=lows[i],
            high=highs[i],
            rank_correlation=rho[i],
            abs_rank_correlation=None if rho[i] is None else abs(rho[i]),  # type: ignore[arg-type]
        )
        for k, i in enumerate(order, start=1)
    ]
    defined = [v for v in res.values if v is not None]
    quant = (
        {
            k: float(q)
            for k, q in zip(
                ("min", "p25", "median", "p75", "max"), np.percentile(defined, [0, 25, 50, 75, 100]), strict=True
            )
        }
        if defined
        else {}
    )
    n_ok = res.n_samples - res.n_failed
    return SensitivityResult(
        target_variable_id=tv,
        target_unit=model.spec.input_by_id(tv).canonical_unit,
        target_low=tlo,
        target_high=thi,
        method=SENSITIVITY_METHOD,
        seed=ss.seed,
        n_samples=res.n_samples,
        n_failed=res.n_failed,
        n_with_breakeven=res.n_defined,
        fraction_with_breakeven=res.n_defined / max(1, n_ok),
        target_quantiles=quant,
        rows=rows,
        skipped=skipped,
        scipy_version=md.version("scipy"),
        limitations=list(SENSITIVITY_LIMITATIONS),
    )
