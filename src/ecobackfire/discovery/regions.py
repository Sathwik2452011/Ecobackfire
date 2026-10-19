"""2-D structured scan of a decision function g(x, y): where is g < 0 (beneficial), where is g > 0, and
where is the boundary between them.

Pure numerics (numpy/scipy + the existing 1-D ``find_breakevens``); no pydantic, so it is testable anywhere.
Convention (same as the 1-D search): g < 0 means 'intervention beneficial'; the boundary is g = 0.

Method: for each of ``nx`` x-values, scan y on ``ny`` points (sign counts + Brent-refined y-roots); then for
each of ``ny`` y-values, scan x on ``nx`` points (x-roots). Doing both directions finds boundaries that are
nearly vertical or nearly horizontal. Every boundary point is a refined root of g(x, y) = 0.

Known limitations (documented, tested where possible): sign changes narrower than the grid spacing and
even-multiplicity zeros are invisible; the distance to the boundary is evaluated on boundary points only,
so it is an upper bound on the true minimum distance (tight to about the grid spacing).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from ecobackfire.errors import ConfigError
from ecobackfire.experiments.breakeven import find_breakevens


@dataclass(frozen=True)
class RegionScan:
    nx: int
    ny: int
    n_beneficial: int
    n_not_beneficial: int
    n_zero: int
    boundary: list[tuple[float, float]]
    suspected_discontinuities: list[tuple[float, float]] = field(default_factory=list)

    @property
    def n_cells(self) -> int:
        return self.n_beneficial + self.n_not_beneficial + self.n_zero

    @property
    def classification(self) -> str:
        if self.n_beneficial and self.n_not_beneficial:
            return "reversal_region"
        if self.n_not_beneficial:
            return "never_beneficial"
        if self.n_beneficial:
            return "always_beneficial"
        return "indeterminate"


def _check(lo: float, hi: float, n: int, what: str) -> None:
    if not (math.isfinite(lo) and math.isfinite(hi) and lo < hi):
        raise ConfigError(f"{what}: requires finite low < high, got [{lo}, {hi}]")
    if not isinstance(n, int) or n < 3:
        raise ConfigError(f"{what}: grid_points must be an integer >= 3")


def scan_region(
    g: Callable[[float, float], float],
    xlo: float,
    xhi: float,
    nx: int,
    ylo: float,
    yhi: float,
    ny: int,
    xtol: float = 1e-12,
) -> RegionScan:
    _check(xlo, xhi, nx, "x axis")
    _check(ylo, yhi, ny, "y axis")
    n_b = n_nb = n_z = 0
    boundary: list[tuple[float, float]] = []
    suspected: list[tuple[float, float]] = []

    for x in np.linspace(xlo, xhi, nx):
        xf = float(x)
        sc = find_breakevens(lambda y, xf=xf: g(xf, y), ylo, yhi, ny, xtol)
        for _, v in sc.scan:
            if v < 0:
                n_b += 1
            elif v > 0:
                n_nb += 1
            else:
                n_z += 1
        for r in sc.roots:
            (suspected if r.kind == "discontinuity_suspected" else boundary).append((xf, r.value))

    for y in np.linspace(ylo, yhi, ny):
        yf = float(y)
        sc = find_breakevens(lambda x, yf=yf: g(x, yf), xlo, xhi, nx, xtol)
        for r in sc.roots:
            (suspected if r.kind == "discontinuity_suspected" else boundary).append((r.value, yf))

    boundary.sort()
    suspected.sort()
    return RegionScan(nx, ny, n_b, n_nb, n_z, boundary, suspected)


def min_normalized_distance(
    boundary: list[tuple[float, float]], bx: float, by: float, xlo: float, xhi: float, ylo: float, yhi: float
) -> float | None:
    """Smallest Euclidean distance from (bx, by) to any boundary point, each axis scaled by its range width."""
    if not boundary:
        return None
    wx, wy = xhi - xlo, yhi - ylo
    return min(math.hypot((px - bx) / wx, (py - by) / wy) for px, py in boundary)
