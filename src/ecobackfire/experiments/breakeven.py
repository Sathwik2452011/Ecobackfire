"""Generic break-even (sign-change) detection on a 1-D decision function g(x).

Convention: g(x) < 0 means 'intervention beneficial'. A break-even is a root of g.

Algorithm: evaluate g on a regular grid; every grid point with g == 0 is a root; every adjacent
pair of strictly non-zero values with opposite signs brackets a root, refined with Brent's method.
A root is only ever reported with its bracket. Known limitation (documented, tested): roots of even
multiplicity and sign changes narrower than the grid spacing cannot be detected.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from scipy.optimize import brentq

from ecobackfire.errors import ConfigError, NumericalError

RootKind = Literal["crossing", "grid_zero", "discontinuity_suspected"]
Side = Literal["below", "above", "unknown"]
Classification = Literal[
    "crossing",
    "no_crossing_always_beneficial",
    "no_crossing_never_beneficial",
    "no_crossing_indeterminate",
]


@dataclass(frozen=True)
class Root:
    value: float
    kind: RootKind
    bracket_low: float | None
    bracket_high: float | None
    g_low: float | None
    g_high: float | None
    residual: float
    iterations: int
    converged: bool
    beneficial_side: Side


@dataclass
class BreakevenScan:
    classification: Classification
    roots: list[Root]
    scan: list[tuple[float, float]]
    note: str
    warnings: list[str] = field(default_factory=list)


def _validate(lo: float, hi: float, n: int, xtol: float) -> None:
    if not (math.isfinite(lo) and math.isfinite(hi)):
        raise ConfigError("search bounds must be finite")
    if not lo < hi:
        raise ConfigError(f"search requires low < high, got [{lo}, {hi}]")
    if not isinstance(n, int) or n < 3:
        raise ConfigError("grid_points must be an integer >= 3")
    if not (math.isfinite(xtol) and xtol > 0):
        raise ConfigError("xtol must be positive and finite")


def find_breakevens(
    g: Callable[[float], float],
    lo: float,
    hi: float,
    n: int = 201,
    xtol: float = 1e-12,
    discontinuity_rel: float = 1e-6,
) -> BreakevenScan:
    _validate(lo, hi, n, xtol)
    xs = np.linspace(lo, hi, n)
    gs: list[float] = []
    for x in xs:
        v = float(g(float(x)))
        if not math.isfinite(v):
            raise NumericalError(f"g({x}) is not finite ({v}); refusing to search")
        gs.append(v)

    roots: list[Root] = []
    for i, v in enumerate(gs):
        if v == 0.0:
            left = gs[i - 1] if i > 0 else None
            right = gs[i + 1] if i < n - 1 else None
            side: Side = "unknown"
            if left is not None and left < 0 and (right is None or right > 0):
                side = "below"
            elif left is not None and left > 0 and (right is None or right < 0):
                side = "above"
            elif left is None and right is not None:
                side = "above" if right < 0 else "below"
            elif right is None and left is not None:
                side = "below" if left < 0 else "above"
            roots.append(Root(float(xs[i]), "grid_zero", None, None, None, None, 0.0, 0, True, side))

    for i in range(n - 1):
        a, b = gs[i], gs[i + 1]
        if a != 0.0 and b != 0.0 and ((a < 0) != (b < 0)):
            xa, xb = float(xs[i]), float(xs[i + 1])
            root, info = brentq(g, xa, xb, xtol=xtol, maxiter=500, full_output=True, disp=False)
            res = float(g(root))
            scale = max(abs(a), abs(b))
            kind: RootKind = "crossing"
            if abs(res) > discontinuity_rel * scale:
                kind = "discontinuity_suspected"
            roots.append(
                Root(
                    float(root),
                    kind,
                    xa,
                    xb,
                    a,
                    b,
                    res,
                    int(info.iterations),
                    bool(info.converged),
                    "below" if a < 0 else "above",
                )
            )
    roots.sort(key=lambda r: r.value)
    scan = [(float(x), v) for x, v in zip(xs, gs, strict=True)]
    warnings = [
        f"root at {r.value:.12g} has |g|={abs(r.residual):.3g} after refinement: possible "
        "discontinuity rather than a continuous crossing"
        for r in roots
        if r.kind == "discontinuity_suspected"
    ]
    if roots:
        cls: Classification = "crossing"
        note = f"{len(roots)} break-even point(s) found on the scan grid."
    elif all(v > 0 for v in gs):
        cls = "no_crossing_never_beneficial"
        note = (
            "No sign change: g > 0 at every grid point, i.e. the intervention is NOT beneficial "
            "anywhere in the search range (already non-beneficial at its most favourable end)."
        )
    elif all(v < 0 for v in gs):
        cls = "no_crossing_always_beneficial"
        note = "No sign change: g < 0 at every grid point, i.e. beneficial throughout the search range."
    else:  # pragma: no cover - zeros always produce roots
        cls = "no_crossing_indeterminate"
        note = "No roots but mixed signs: indeterminate."
    if not roots:
        note += (
            " Even-multiplicity roots or sign changes narrower than the grid spacing cannot be excluded by this method."
        )
    return BreakevenScan(cls, roots, scan, note, warnings)
