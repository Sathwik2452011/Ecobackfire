"""Seeded quasi-Monte-Carlo sampling and rank-correlation screening. Pure numerics (numpy/scipy).

The sample is a scrambled Sobol sequence: reproducible for a given (dimension, m, seed) and scipy version.
Spearman rank correlation between each sampled parameter and a scalar target is a SCREENING measure of
monotone influence. It is not a variance-based (Sobol) index, and it says nothing about interactions.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from scipy import stats
from scipy.stats import qmc

from ecobackfire.errors import ConfigError, EcoBackfireError


def sobol_unit(d: int, m: int, seed: int) -> np.ndarray:
    if d < 1:
        raise ConfigError("at least one sampled dimension is required")
    if not 1 <= m <= 20:
        raise ConfigError("m must be between 1 and 20 (2**m samples)")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ConfigError("seed must be a non-negative integer")
    return qmc.Sobol(d=d, scramble=True, seed=seed).random_base2(m)


def scale_points(unit: np.ndarray, lows: Sequence[float], highs: Sequence[float]) -> np.ndarray:
    lo, hi = np.asarray(lows, dtype=float), np.asarray(highs, dtype=float)
    if lo.shape != hi.shape or lo.shape[0] != unit.shape[1]:
        raise ConfigError("lows/highs must match the sample dimension")
    if not np.all(lo < hi) or not (np.all(np.isfinite(lo)) and np.all(np.isfinite(hi))):
        raise ConfigError("every sampled range requires finite low < high")
    return lo + unit * (hi - lo)


@dataclass(frozen=True)
class SamplingResult:
    points: np.ndarray  # (n, d) in the caller's units
    values: list[float | None]  # None where the target is undefined (e.g. no break-even) or evaluation failed
    failed: list[bool]  # True where evaluation raised an EcoBackfireError

    @property
    def n_samples(self) -> int:
        return len(self.values)

    @property
    def n_defined(self) -> int:
        return sum(v is not None for v in self.values)

    @property
    def n_failed(self) -> int:
        return sum(self.failed)


def sample_function(
    f: Callable[[np.ndarray], float | None],
    lows: Sequence[float],
    highs: Sequence[float],
    m: int,
    seed: int,
) -> SamplingResult:
    pts = scale_points(sobol_unit(len(lows), m, seed), lows, highs)
    vals: list[float | None] = []
    failed: list[bool] = []
    for p in pts:
        try:
            v = f(p)
            vals.append(None if v is None else float(v))
            failed.append(False)
        except EcoBackfireError:
            vals.append(None)
            failed.append(True)
    return SamplingResult(pts, vals, failed)


def rank_correlations(points: np.ndarray, values: Sequence[float | None]) -> list[float | None]:
    """Spearman rho of each column against the defined values; None if undefined or constant."""
    idx = [i for i, v in enumerate(values) if v is not None and math.isfinite(v)]
    out: list[float | None] = [None] * points.shape[1]
    if len(idx) < 3:
        return out
    y = np.array([values[i] for i in idx], dtype=float)
    if np.ptp(y) == 0.0:
        return out
    for j in range(points.shape[1]):
        col = points[idx, j]
        if np.ptp(col) == 0.0:
            continue
        rho = float(stats.spearmanr(col, y).statistic)
        out[j] = None if math.isnan(rho) else rho
    return out
