"""Automated backfire hunt: budgeted, deterministic, non-monotone-safe search for where a benefit is lost.

Pure numerics (numpy/scipy + the existing ``find_breakevens``/``Root``); no pydantic, no model knowledge. The decision
function is the project's existing ``g`` (``make_g``): ``g < 0`` beneficial, ``g > 0`` adverse, a root of ``g`` is a
break-even. Nothing here defines an environmental equation or decides what a "backfire" is scientifically: it finds
sign changes of ``g`` and reports them with the evaluations spent.

Phases (all evaluations of ``g`` go through one counter; the cap is exact and never exceeded)

1. COARSE SCAN of every parameter with the existing ``find_breakevens`` (grid + Brent). Ordinary crossings are
   therefore exactly the ones the existing engine and the downstream falsification re-solve find.
2. SUB-GRID REFINEMENT. The grid scan cannot see a reversal region narrower than one grid cell, nor an
   even-multiplicity root (a tangent touch). Both leave a footprint: a grid point that is a strict local minimum of
   ``g`` while ``g > 0`` (or a strict local maximum while ``g < 0``) - the function turned around without crossing.
   Each such suspect is re-scanned locally (``refine_points``) and polished with bounded minimisation; if the
   extremum reaches the other side of zero, both crossings are refined with Brent and the pair becomes a NARROW
   reversal region; if it reaches zero within ``touch_tol`` it is a tangent touch (break-even WITHOUT reversal).
   Suspects are processed closest-to-zero first, so a short budget is spent where a crossing is most likely.
3. REGIME INTERVALS: the sorted boundaries split each range into maximal intervals of one sign (beneficial /
   adverse); several separated adverse intervals are reported as several reversal regions. No extra evaluations.

Budget: every call of ``g`` is counted. When the cap is reached the search stops and the partial result is returned
with ``status = "budget_exhausted"``: parameters not scanned, scans aborted mid-way and suspects left unrefined are all
recorded - nothing silently disappears.

Known limits (documented, tested where possible): a reversal narrower than the local re-scan resolution, a
reversal between the range edge and the first grid point, a plateau (equal neighbouring values) hiding a dip, and
sub-structure inside one refined cell are not detected. Absence of a finding is never proof of absence.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from scipy.optimize import brentq, minimize_scalar

from ecobackfire.errors import EcoBackfireError
from ecobackfire.experiments.breakeven import BreakevenScan, Root, find_breakevens

G = Callable[[float], float]
Regime = Literal["beneficial", "adverse", "indeterminate"]


class BudgetExhausted(Exception):  # noqa: N818 - control-flow signal, not an error condition
    """Raised by the counting wrapper when the next evaluation would exceed the cap."""


class Counter:
    """Counts every evaluation of ``g`` and refuses to exceed ``limit`` (exact cap)."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("evaluation limit must be >= 1")
        self.limit = limit
        self.used = 0

    @property
    def remaining(self) -> int:
        return self.limit - self.used

    def wrap(self, g: G) -> G:
        def counted(x: float) -> float:
            if self.used >= self.limit:
                raise BudgetExhausted
            self.used += 1
            return g(float(x))

        return counted


@dataclass(frozen=True)
class HuntConfig:
    max_evaluations: int
    narrow_fraction: float = 0.05
    near_break_even_tol: float = 0.01
    refine_points: int = 17
    touch_tol: float = 1e-9
    max_local_points: int = 2001
    local_margin_cells: float = 2.0  # candidate window = refined cell pair widened by this many grid cells per side
    local_points_per_dip: int = 8  # local grid spacing target = dip width / this


@dataclass(frozen=True)
class ParamTask:
    var: str
    g: G
    lo: float
    hi: float
    n: int
    xtol: float
    scale: float  # reference scale (|C|) used to judge "zero" in relative terms


@dataclass
class HuntRoot:
    """A crossing found by the hunt. ``k`` is its index among ALL roots of the variable, sorted by value."""

    root: Root
    origin: Literal["global_grid", "local_refinement"]
    window: tuple[float, float, int]  # search window a downstream re-solve can resolve this root in
    resolution_limited: bool = False  # the local window could not reach the target spacing (cap on grid points)
    dip: int | None = None
    k: int = -1


@dataclass(frozen=True)
class Touch:
    x: float
    g: float
    cell: tuple[float, float]
    origin: str  # "grid_zero" or "local_refinement"


@dataclass(frozen=True)
class Interval:
    low: float
    high: float
    regime: Regime
    lower_kind: Literal["range_edge", "candidate", "rejected"]
    lower_k: int | None  # index of the bounding root when ``lower_kind == "candidate"``
    upper_kind: Literal["range_edge", "candidate", "rejected"]
    upper_k: int | None


@dataclass
class ParamHunt:
    task: ParamTask
    status: Literal["searched", "budget_exhausted", "failed"] = "searched"
    note: str = ""
    scan: BreakevenScan | None = None
    roots: list[HuntRoot] = field(default_factory=list)  # all roots (grid + refined), sorted, ``k`` assigned
    rejected_grid_roots: list[Root] = field(default_factory=list)  # discontinuity / not converged (never candidates)
    touches: list[Touch] = field(default_factory=list)
    intervals: list[Interval] = field(default_factory=list)
    n_extrema_refined: int = 0
    n_extrema_unrefined: int = 0
    min_abs_g: float | None = None
    evaluations: int = 0

    @property
    def behaviour(self) -> str:
        if self.scan is None:
            return "not_searched"
        if self.roots or self.rejected_grid_roots:
            return "reverses"
        if self.touches:
            return "touches_break_even"
        gs = [v for _, v in self.scan.scan]
        if all(v < 0 for v in gs):
            return "remains_beneficial"
        if all(v > 0 for v in gs):
            return "never_beneficial_in_range"
        return "reverses"  # pragma: no cover - mixed signs always produce roots


@dataclass
class HuntOutcome:
    params: list[ParamHunt]
    evaluations: int
    max_evaluations: int
    status: Literal["complete", "budget_exhausted"]


def _sgn(v: float) -> int:
    return (v > 0) - (v < 0)


def _brent_root(g: G, a: float, ga: float, b: float, gb: float, xtol: float) -> Root:
    root, info = brentq(g, a, b, xtol=xtol, maxiter=500, full_output=True, disp=False)
    res = float(g(root))
    return Root(
        float(root),
        "crossing",
        a,
        b,
        ga,
        gb,
        res,
        int(info.iterations),
        bool(info.converged),
        "below" if ga < 0 else "above",
    )


# ------------------------------------------------------------------ phase 2: one suspect extremum


@dataclass
class _Refined:
    roots: list[Root] = field(default_factory=list)
    touch: Touch | None = None
    closest: float | None = None  # closest approach to zero from the grid side, in g units (>= 0)
    inner_sign: int = 0  # sign of g at the extremum when it crossed


def _refine_extremum(
    task: ParamTask, g: G, xs: np.ndarray, gs: list[float], i: int, cfg: HuntConfig
) -> _Refined:
    side = _sgn(gs[i])  # +1: positive local min (looking for a dip below zero); -1: negative local max
    a, b = float(xs[i - 1]), float(xs[i + 1])
    pts = np.linspace(a, b, cfg.refine_points)
    vals = [side * g(float(p)) for p in pts]  # u = side * g ; u > 0 is "same side as the grid"
    j = int(np.argmin(vals))
    jl, jh = max(j - 1, 0), min(j + 1, len(pts) - 1)
    xm, um = float(pts[j]), vals[j]
    if 0 < j < len(pts) - 1 and um > 0:  # polish only if the local scan has not already crossed
        res = minimize_scalar(
            lambda x: side * g(float(x)),
            bounds=(float(pts[jl]), float(pts[jh])),
            method="bounded",
            options={"xatol": (b - a) * 1e-12, "maxiter": 200},
        )
        if math.isfinite(float(res.fun)) and float(res.fun) < um:
            xm, um = float(res.x), float(res.fun)
    out = _Refined()
    touch_abs = cfg.touch_tol * task.scale
    if um < -touch_abs:  # the function really goes to the other side of zero between the grid points
        gm = side * um
        left = _brent_root(g, a, gs[i - 1], xm, gm, task.xtol)
        right = _brent_root(g, xm, gm, b, gs[i + 1], task.xtol)
        out.roots = [left, right]
        out.inner_sign = -side
    elif abs(um) <= touch_abs:
        out.touch = Touch(xm, side * um, (a, b), "local_refinement")
    else:
        out.closest = um
    return out


def _suspects(gs: list[float]) -> list[tuple[int, int]]:
    """(index, sign) of interior grid points that are strict local extrema moving AWAY from the zero line."""
    out = []
    for i in range(1, len(gs) - 1):
        v = gs[i]
        if v > 0 and v < gs[i - 1] and v < gs[i + 1]:
            out.append((i, 1))
        elif v < 0 and v > gs[i - 1] and v > gs[i + 1]:
            out.append((i, -1))
    return out


# ------------------------------------------------------------------ phase 3: intervals


def _build_intervals(ph: ParamHunt, dip_inner_sign: dict[int, int]) -> list[Interval]:
    assert ph.scan is not None
    t = ph.task
    xs = np.array([x for x, _ in ph.scan.scan])
    gs = np.array([v for _, v in ph.scan.scan])
    # (value, kind, root-or-None): discontinuity / unconverged grid roots still split the sign structure
    edges: list[tuple[float, str, HuntRoot | None]] = [(r.root.value, "candidate", r) for r in ph.roots]
    edges += [(r.value, "rejected", None) for r in ph.rejected_grid_roots]
    edges.sort(key=lambda e: e[0])
    edges = [(t.lo, "range_edge", None), *edges, (t.hi, "range_edge", None)]
    raw: list[Interval] = []
    for (p, pk, pr), (q, qk, qr) in itertools.pairwise(edges):
        if not q > p:
            continue
        regime: Regime = "indeterminate"
        inside = np.where((xs > p) & (xs < q) & (gs != 0.0))[0]
        if inside.size:  # sign of the grid point nearest the middle of the interval
            idx = int(inside[np.argmin(np.abs(xs[inside] - 0.5 * (p + q)))])
            regime = "beneficial" if gs[idx] < 0 else "adverse"
        elif pr is not None and qr is not None and pr.dip is not None and pr.dip == qr.dip:
            regime = "beneficial" if dip_inner_sign[pr.dip] < 0 else "adverse"  # refined narrow region
        raw.append(
            Interval(
                float(p), float(q), regime, pk, pr.k if pr is not None else None,  # type: ignore[arg-type]
                qk, qr.k if qr is not None else None,  # type: ignore[arg-type]
            )
        )
    merged: list[Interval] = []  # a rejected boundary (no sign change) does not separate two intervals of one sign
    for iv in raw:
        if merged and merged[-1].regime == iv.regime and merged[-1].upper_kind == "rejected":
            m = merged[-1]
            merged[-1] = Interval(m.low, iv.high, m.regime, m.lower_kind, m.lower_k, iv.upper_kind, iv.upper_k)
        else:
            merged.append(iv)
    return merged


# ------------------------------------------------------------------ the hunt


def run_hunt(tasks: list[ParamTask], cfg: HuntConfig) -> HuntOutcome:
    counter = Counter(cfg.max_evaluations)
    hunts = [ParamHunt(t) for t in tasks]
    exhausted = False

    # phase 1: coarse scans with the existing find_breakevens (+ Brent), counted
    for ph in hunts:
        t = ph.task
        if exhausted or counter.remaining < t.n:
            ph.status, exhausted = "budget_exhausted", True
            ph.note = f"not scanned: {counter.remaining} evaluation(s) left, a scan needs {t.n}"
            continue
        before = counter.used
        try:
            ph.scan = find_breakevens(counter.wrap(t.g), t.lo, t.hi, t.n, t.xtol)
        except BudgetExhausted:
            ph.status, exhausted = "budget_exhausted", True
            ph.note = "evaluation budget exhausted during this parameter's scan/refinement; its result is discarded"
        except EcoBackfireError as exc:
            ph.status = "failed"
            ph.note = f"{type(exc).__name__}: {exc}"
        ph.evaluations = counter.used - before

    # classify grid roots; grid zeros with the same strict sign on both sides are tangent touches, not crossings
    for ph in hunts:
        if ph.scan is None:
            continue
        gs = [v for _, v in ph.scan.scan]
        xs = [x for x, _ in ph.scan.scan]
        for r in ph.scan.roots:
            if r.kind == "grid_zero":
                i = xs.index(r.value) if r.value in xs else -1
                if 0 < i < len(xs) - 1 and _sgn(gs[i - 1]) == _sgn(gs[i + 1]) != 0:
                    ph.touches.append(Touch(r.value, 0.0, (xs[i - 1], xs[i + 1]), "grid_zero"))
                    continue
            if r.kind == "discontinuity_suspected" or not r.converged:
                ph.rejected_grid_roots.append(r)
                continue
            ph.roots.append(HuntRoot(r, "global_grid", (ph.task.lo, ph.task.hi, ph.task.n)))
        ph.min_abs_g = min(abs(v) for v in gs)

    # phase 2: sub-grid refinement of suspect extrema, closest to zero first (deterministic order)
    queue: list[tuple[float, int, int, int]] = []  # (|g|/scale, param index, grid index, sign)
    for pi, ph in enumerate(hunts):
        if ph.scan is None:
            continue
        gs = [v for _, v in ph.scan.scan]
        for i, s in _suspects(gs):
            queue.append((abs(gs[i]) / ph.task.scale, pi, i, s))
    queue.sort()
    dip_inner: dict[tuple[int, int], int] = {}
    n_dip = 0
    stopped_at = len(queue)
    for qi, (_, pi, i, _s) in enumerate(queue):
        ph = hunts[pi]
        assert ph.scan is not None
        xs = np.array([x for x, _ in ph.scan.scan])
        gs = [v for _, v in ph.scan.scan]
        before = counter.used
        try:
            ref = _refine_extremum(ph.task, counter.wrap(ph.task.g), xs, gs, i, cfg)
        except BudgetExhausted:
            exhausted, stopped_at = True, qi
            ph.evaluations += counter.used - before
            break
        ph.evaluations += counter.used - before
        ph.n_extrema_refined += 1
        if ref.touch is not None:
            ph.touches.append(ref.touch)
        elif ref.closest is not None:
            ph.min_abs_g = min(ph.min_abs_g if ph.min_abs_g is not None else math.inf, ref.closest)
        elif ref.roots:
            h = float(xs[1] - xs[0])
            a, b = float(xs[i - 1]), float(xs[i + 1])
            w_lo = max(ph.task.lo, a - cfg.local_margin_cells * h)
            w_hi = min(ph.task.hi, b + cfg.local_margin_cells * h)
            width = ref.roots[1].value - ref.roots[0].value
            target = width / cfg.local_points_per_dip
            n_loc = math.ceil((w_hi - w_lo) / target) + 1 if target > 0 else cfg.max_local_points
            limited = n_loc > cfg.max_local_points
            n_loc = max(min(n_loc, cfg.max_local_points), 41)
            for r in ref.roots:
                ph.roots.append(HuntRoot(r, "local_refinement", (w_lo, w_hi, n_loc), limited, n_dip))
            dip_inner[(pi, n_dip)] = ref.inner_sign
            ph.min_abs_g = 0.0 if ph.min_abs_g is None else min(ph.min_abs_g, 0.0)
            n_dip += 1
    for qi in range(stopped_at, len(queue)):
        hunts[queue[qi][1]].n_extrema_unrefined += 1

    # finalise: sort, index, intervals
    for pi, ph in enumerate(hunts):
        ph.roots.sort(key=lambda r: r.root.value)
        for k, r in enumerate(ph.roots):
            r.k = k
        if ph.scan is not None:
            ph.intervals = _build_intervals(ph, {d: s for (p, d), s in dip_inner.items() if p == pi})
    return HuntOutcome(
        hunts, counter.used, cfg.max_evaluations, "budget_exhausted" if exhausted else "complete"
    )
