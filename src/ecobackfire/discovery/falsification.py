"""Deterministic falsification checks for a 1-D break-even candidate.  Pure numerics (numpy/scipy + the
existing ``find_breakevens``); no pydantic, so it is testable anywhere.

Question asked of every candidate: *how easily can it be proved wrong?*  Each check can FAIL, and a failed
check is reported, never dropped.  Nothing here says a candidate is true; surviving checks only means these
particular attempts did not refute it.

Vocabulary reuses ``contracts.verification.CheckStatus``: passed | failed | limits_claim | inconclusive |
skipped.  ``limits_claim`` means "survived the minimal test but not the larger ones".  ``failed`` carries a
``category`` (false_crossing, numerical_instability, boundary_dependence, not_reproduced,
perturbation_fragile, not_independently_reproduced, false_no_crossing) so later classification can map it.

Conventions: g(x) < 0 means 'beneficial'; a candidate is a root x* of g in the search range [lo, hi] of
width w = hi - lo.  All thresholds live in ``FalsificationConfig`` and are recorded in the report.  They are
documented POLICY choices, not derived from data; changing them changes outcomes.

Metric definitions (METRIC_DEFINITIONS is copied into reports)
  X1 crossing validity    probes h_k = max(q_k * w, 10 * xtol), q_k in probe_fractions, kept only if
                          x* +- h_k lies inside [lo, hi].  Valid iff for EVERY usable probe
                          sign(g(x*-h)) != sign(g(x*+h)), both non-zero, the pattern equals the claimed
                          side, and the continuity ratio rho = |g(x*)| / max(|g(x*-h_min)|, |g(x*+h_min)|)
                          is <= max_continuity_ratio.  (Touching roots fail the sign test; jumps fail rho.)
  X2 numerical stability  (a) refinement: re-scan on grids of n, 2n-1, 4n-3 points; the root nearest x* must
                          lie within agree_tol * w of x*.  (b) independent solver: 200-step bisection on
                          the bracket (not Brent).  (c) noise: g -> g +- noise_rel * G, G = max(|g(lo)|,
                          |g(hi)|); shift = max |x*_noisy - x*| / w (by bisection) must be <= noise_tol.
                          A root whose position moves more than noise_tol under a 1e-10-relative change of g
                          is not numerically resolvable.
  X3 boundary dependence  margin m = min(x* - lo, hi - x*) / w must be >= edge_margin; and the root must be
                          recovered (within agree_tol * w) when the range is expanded by range_expansion*w
                          per side (clipped to ``domain``).  Further roots found OUTSIDE [lo, hi] limit
                          the claim (limits_claim); other roots inside [lo, hi] do not.
  X4 reproducibility      the whole search is repeated ``repeats`` times; roots must be bit-identical.
  X5 perturbation (OAT)   for each other input p, level L, sign s: p' = p (1 + s L); re-scan [lo, hi].
                          preserved iff a converged crossing exists with the same beneficial side.
                          preserved_fraction(L) = n_preserved(L) / n_valid(L); perturbations whose g raises
                          an EcoBackfireError (e.g. out-of-domain input) are counted in n_invalid and
                          excluded from the denominator.  failed if fraction at the smallest L < 1;
                          limits_claim if only larger L lose it.  Sensitivity coefficient
                          S_p = (x*(p(1+L0)) - x*(p(1-L0))) / (2 L0 x*) = d ln x* / d ln p (finite difference
                          at the smallest level; None if x* == 0 or a side is lost).
  X6 independent roots    caller-supplied roots from independent code (closed form, Decimal): one must lie
                          within agree_tol * w of x*; an EMPTY list means the independent path finds no
                          root, which fails the candidate.
  X7 no-crossing verdict  (separate entry point) re-scan at ``dense_factor`` x the grid; any sign change
                          found falsifies 'no crossing'; an interior local minimum of |g| below
                          near_zero_rel * G leaves it inconclusive (possible even-multiplicity root).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Literal

import numpy as np

from ecobackfire.core.hashing import canonical_json, sha256_text
from ecobackfire.errors import ConfigError, EcoBackfireError
from ecobackfire.experiments.breakeven import BreakevenScan, Root, find_breakevens

Status = Literal["passed", "failed", "limits_claim", "inconclusive", "skipped"]
Category = Literal[
    "false_crossing",
    "numerical_instability",
    "boundary_dependence",
    "not_reproduced",
    "perturbation_fragile",
    "not_independently_reproduced",
    "false_no_crossing",
    # Evidence-sensitive challenges (added with discovery/evidence_sensitivity.py; additive):
    "evidence_range_sensitive",
    "source_disagreement",
]
MakeG = Callable[[Mapping[str, float]], Callable[[float], float]]
Metric = float | int | str | bool | None

METRIC_DEFINITIONS: dict[str, str] = {
    "X1": "sign change at every usable probe h_k and continuity ratio rho = |g(x*)| / max side |g| <= limit",
    "X2": "refined grids, independent bisection and 1e-10-relative noise must move the root by <= tolerance * w",
    "X3": "margin = min(x*-lo, hi-x*)/w >= edge_margin and root persists in the expanded range",
    "X4": "repeated identical searches return bit-identical roots",
    "X5": "preserved_fraction(L) = n_preserved/n_valid over one-at-a-time perturbations; S_p = d ln x* / d ln p",
    "X6": "an independently computed root lies within agree_tol * w of x*",
    "X7": "dense re-scan finds no sign change and no near-zero interior minimum of |g|",
}


@dataclass(frozen=True)
class FalsificationConfig:
    """Every number that influences an outcome. Recorded verbatim in the report."""

    probe_fractions: tuple[float, ...] = (1e-6, 1e-4, 1e-2)
    max_continuity_ratio: float = 0.1
    agree_tol: float = 1e-8
    noise_rel: float = 1e-10
    noise_tol: float = 1e-6
    edge_margin: float = 0.02
    range_expansion: float = 0.25
    repeats: int = 3
    perturbation_levels: tuple[float, ...] = (0.01, 0.05, 0.10)
    dense_factor: int = 16
    near_zero_rel: float = 1e-6

    def validate(self) -> None:
        pf = self.probe_fractions
        if not pf or any(not (0 < q < 0.5) for q in pf) or list(pf) != sorted(set(pf)):
            raise ConfigError("probe_fractions must be strictly increasing values in (0, 0.5)")
        if not 0 < self.max_continuity_ratio < 1:
            raise ConfigError("max_continuity_ratio must be in (0, 1)")
        for name in ("agree_tol", "noise_rel", "noise_tol"):
            v = getattr(self, name)
            if not (math.isfinite(v) and 0 < v < 1):
                raise ConfigError(f"{name} must be in (0, 1)")
        if not 0 <= self.edge_margin < 0.5:
            raise ConfigError("edge_margin must be in [0, 0.5)")
        if not (math.isfinite(self.range_expansion) and 0 < self.range_expansion <= 2):
            raise ConfigError("range_expansion must be in (0, 2]")
        if not isinstance(self.repeats, int) or isinstance(self.repeats, bool) or self.repeats < 2:
            raise ConfigError("repeats must be an integer >= 2")
        lv = self.perturbation_levels
        if not lv or any(not (0 < x < 1) for x in lv) or list(lv) != sorted(set(lv)):
            raise ConfigError("perturbation_levels must be strictly increasing values in (0, 1)")
        if not isinstance(self.dense_factor, int) or self.dense_factor < 2:
            raise ConfigError("dense_factor must be an integer >= 2")
        if not (math.isfinite(self.near_zero_rel) and 0 < self.near_zero_rel < 1):
            raise ConfigError("near_zero_rel must be in (0, 1)")


@dataclass(frozen=True)
class FalsificationCheck:
    check_id: str
    category: Category
    status: Status
    summary: str
    metrics: dict[str, Metric] = field(default_factory=dict)
    tolerance: str = ""


@dataclass(frozen=True)
class FalsificationReport:
    subject: str  # e.g. "epsilon#0" or "no-crossing(f)"
    root: float | None
    search_low: float
    search_high: float
    config: dict[str, object]
    checks: list[FalsificationCheck]
    metric_definitions: dict[str, str]

    @property
    def failed(self) -> list[FalsificationCheck]:
        return [c for c in self.checks if c.status == "failed"]

    @property
    def falsified(self) -> bool:
        return bool(self.failed)

    @property
    def limits(self) -> list[FalsificationCheck]:
        return [c for c in self.checks if c.status == "limits_claim"]

    @property
    def survived(self) -> list[FalsificationCheck]:
        return [c for c in self.checks if c.status == "passed"]

    @property
    def not_assessed(self) -> list[FalsificationCheck]:
        return [c for c in self.checks if c.status in ("inconclusive", "skipped")]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def sha256(self) -> str:
        return sha256_text(canonical_json(self.to_dict()))


# ------------------------------------------------------------------ helpers


def _finite(v: float) -> float | None:
    """Reports never contain inf/NaN (they cannot be canonically hashed); None means 'no such value'."""
    return float(v) if math.isfinite(v) else None


def _sgn(v: float) -> int:
    return (v > 0) - (v < 0)


def _bisect(g: Callable[[float], float], a: float, b: float, steps: int = 200) -> float | None:
    """Plain bisection (deliberately not Brent). None if the ends do not bracket a sign change."""
    ga, gb = g(a), g(b)
    if ga == 0.0:
        return a
    if gb == 0.0:
        return b
    if _sgn(ga) == _sgn(gb):
        return None
    for _ in range(steps):
        m = 0.5 * (a + b)
        gm = g(m)
        if gm == 0.0:
            return m
        if _sgn(gm) == _sgn(ga):
            a, ga = m, gm
        else:
            b = m
    return 0.5 * (a + b)


def _nearest(roots: Sequence[float], x: float) -> float | None:
    return min(roots, key=lambda r: abs(r - x)) if roots else None


def _probes(cfg: FalsificationConfig, x: float, lo: float, hi: float, xtol: float) -> list[float]:
    w = hi - lo
    hs = sorted({max(q * w, 10.0 * xtol) for q in cfg.probe_fractions})
    return [h for h in hs if lo <= x - h and x + h <= hi]


def _expected_signs(side: str) -> tuple[int, int] | None:
    return {"below": (-1, 1), "above": (1, -1)}.get(side)


# ------------------------------------------------------------------ checks


def check_crossing(
    g: Callable[[float], float], root: Root, lo: float, hi: float, xtol: float, cfg: FalsificationConfig
) -> FalsificationCheck:
    x = root.value
    tol = f"probes {cfg.probe_fractions} x w; rho <= {cfg.max_continuity_ratio}"
    hs = _probes(cfg, x, lo, hi, xtol)
    if not hs:
        return FalsificationCheck("X1_crossing_validity", "false_crossing", "inconclusive",
                                  "no probe fits inside the search range around the root", {}, tol)
    try:
        rows = [(h, g(x - h), g(x + h)) for h in hs]
        gx = g(x)
    except EcoBackfireError as exc:
        return FalsificationCheck("X1_crossing_validity", "false_crossing", "failed",
                                  f"model evaluation failed next to the root: {type(exc).__name__}: {exc}", {}, tol)
    bad = [h for h, a, b in rows if not (a != 0 and b != 0 and _sgn(a) != _sgn(b))]
    _, a0, b0 = rows[0]
    rho = abs(gx) / max(abs(a0), abs(b0), 1e-300)
    exp = _expected_signs(root.beneficial_side)
    wrong_dir = exp is not None and any((_sgn(a), _sgn(b)) != exp for _, a, b in rows if _sgn(a) != _sgn(b))
    metrics: dict[str, Metric] = {
        "n_probes": len(hs), "n_probes_without_sign_change": len(bad),
        "continuity_ratio": rho, "root_kind": root.kind, "direction_matches_claim": not wrong_dir,
    }
    if bad:
        return FalsificationCheck("X1_crossing_validity", "false_crossing", "failed",
                                  f"no sign change across the root at probe distance(s) {bad}: touching root or "
                                  "false crossing", metrics, tol)
    if wrong_dir:
        return FalsificationCheck("X1_crossing_validity", "false_crossing", "failed",
                                  "sign pattern around the root contradicts the claimed beneficial side", metrics, tol)
    if rho > cfg.max_continuity_ratio or root.kind == "discontinuity_suspected":
        return FalsificationCheck("X1_crossing_validity", "false_crossing", "failed",
                                  f"|g(x*)| is {rho:.3g} of the neighbouring values: a jump, not a continuous "
                                  "crossing", metrics, tol)
    return FalsificationCheck("X1_crossing_validity", "false_crossing", "passed",
                              f"sign change confirmed at {len(hs)} probe distance(s); continuity ratio {rho:.3g}",
                              metrics, tol)


def _bracket(g: Callable[[float], float], root: Root, lo: float, hi: float, xtol: float, cfg: FalsificationConfig
             ) -> tuple[float, float] | None:
    if root.bracket_low is not None and root.bracket_high is not None:
        return root.bracket_low, root.bracket_high
    for h in _probes(cfg, root.value, lo, hi, xtol):
        a, b = g(root.value - h), g(root.value + h)
        if a != 0 and b != 0 and _sgn(a) != _sgn(b):
            return root.value - h, root.value + h
    return None


def check_numerical_stability(
    g: Callable[[float], float], root: Root, lo: float, hi: float, n: int, xtol: float, cfg: FalsificationConfig
) -> FalsificationCheck:
    x, w = root.value, hi - lo
    tol = f"agree_tol={cfg.agree_tol}, noise_rel={cfg.noise_rel}, noise_tol={cfg.noise_tol} (x w)"
    cid = "X2_numerical_stability"
    try:
        br = _bracket(g, root, lo, hi, xtol, cfg)
        if br is None:
            return FalsificationCheck(cid, "numerical_instability", "inconclusive",
                                      "no sign-changing bracket around the root", {}, tol)
        # (a) grid refinement
        devs: dict[str, float] = {}
        for k, nn in (("2n-1", 2 * n - 1), ("4n-3", 4 * n - 3)):
            near = _nearest([r.value for r in find_breakevens(g, lo, hi, nn, xtol).roots], x)
            devs[f"refined_{k}"] = math.inf if near is None else abs(near - x) / w
        # (b) independent solver
        xb = _bisect(g, br[0], br[1])
        devs["bisection"] = math.inf if xb is None else abs(xb - x) / w
        # (c) noise
        gs = max(abs(g(lo)), abs(g(hi)), 1e-300)
        shifts = []
        for s in (-1.0, 1.0):
            xn = _bisect(lambda t, s=s: g(t) + s * cfg.noise_rel * gs, br[0], br[1])
            shifts.append(math.inf if xn is None else abs(xn - x) / w)
        noise_shift = max(shifts)
    except EcoBackfireError as exc:
        return FalsificationCheck(cid, "numerical_instability", "failed",
                                  f"model evaluation failed during stability checks: {exc}", {}, tol)
    path_dev = max(devs.values())
    metrics: dict[str, Metric] = {
        **{k: _finite(v) for k, v in devs.items()},
        "max_path_deviation_over_w": _finite(path_dev),
        "noise_shift_over_w": _finite(noise_shift),
        "paths_without_root": ",".join(k for k, v in devs.items() if not math.isfinite(v)),
    }
    if path_dev > cfg.agree_tol:
        return FalsificationCheck(cid, "numerical_instability", "failed",
                                  f"independent calculation paths disagree by {path_dev:.3g} x w", metrics, tol)
    if noise_shift > cfg.noise_tol:
        return FalsificationCheck(cid, "numerical_instability", "failed",
                                  f"a {cfg.noise_rel:g}-relative change of g moves the root by {noise_shift:.3g} x w: "
                                  "ill-conditioned", metrics, tol)
    return FalsificationCheck(cid, "numerical_instability", "passed",
                              f"paths agree within {path_dev:.3g} x w; noise shift {noise_shift:.3g} x w", metrics, tol)


def check_boundary(
    g: Callable[[float], float], root: Root, lo: float, hi: float, n: int, xtol: float, cfg: FalsificationConfig,
    domain: tuple[float, float] | None = None,
) -> FalsificationCheck:
    x, w = root.value, hi - lo
    cid, tol = "X3_boundary_dependence", f"edge_margin={cfg.edge_margin}, expansion={cfg.range_expansion}"
    margin = min(x - lo, hi - x) / w
    dlo, dhi = domain if domain else (-math.inf, math.inf)
    elo, ehi = max(lo - cfg.range_expansion * w, dlo), min(hi + cfg.range_expansion * w, dhi)
    metrics: dict[str, Metric] = {"margin_over_w": margin, "expanded_low": elo, "expanded_high": ehi}
    if margin < cfg.edge_margin:
        return FalsificationCheck(cid, "boundary_dependence", "failed",
                                  f"root lies {margin:.3g} x w from the nearest configured edge (< {cfg.edge_margin}): "
                                  "its existence depends on where the range was drawn", metrics, tol)
    if (elo, ehi) == (lo, hi):
        return FalsificationCheck(cid, "boundary_dependence", "passed",
                                  f"margin {margin:.3g} x w; range cannot be expanded within the domain", metrics, tol)
    try:
        n_exp = max(n, round(n * (ehi - elo) / w) | 1)
        roots = [r.value for r in find_breakevens(g, elo, ehi, n_exp, xtol).roots]
    except EcoBackfireError as exc:
        return FalsificationCheck(cid, "boundary_dependence", "limits_claim",
                                  f"expanded range not evaluable ({type(exc).__name__}: {exc}); the finding is only "
                                  "known inside the configured range", metrics, tol)
    near = _nearest(roots, x)
    outside = [r for r in roots if r < lo or r > hi]
    metrics["n_roots_expanded"] = len(roots)
    metrics["n_new_roots_outside_range"] = len(outside)
    if near is None or abs(near - x) / w > cfg.agree_tol:
        return FalsificationCheck(cid, "boundary_dependence", "failed",
                                  "root not recovered when the range is expanded", metrics, tol)
    if outside:
        return FalsificationCheck(cid, "boundary_dependence", "limits_claim",
                                  f"root persists, but expanding the range reveals {len(outside)} further break-even "
                                  f"point(s) outside it (first at {outside[0]:.9g}): the finding is only complete "
                                  "within the configured range", metrics, tol)
    return FalsificationCheck(cid, "boundary_dependence", "passed",
                              f"margin {margin:.3g} x w and root persists in the expanded range", metrics, tol)


def check_reproducibility(
    g: Callable[[float], float], root: Root, lo: float, hi: float, n: int, xtol: float, cfg: FalsificationConfig
) -> FalsificationCheck:
    cid, tol = "X4_reproducibility", f"bit-identical over {cfg.repeats} repeats"
    try:
        runs = [[r.value for r in find_breakevens(g, lo, hi, n, xtol).roots] for _ in range(cfg.repeats)]
    except EcoBackfireError as exc:
        return FalsificationCheck(cid, "not_reproduced", "failed", f"repeat search failed: {exc}", {}, tol)
    metrics: dict[str, Metric] = {"repeats": cfg.repeats, "n_distinct_outcomes": len({tuple(r) for r in runs})}
    if root.value not in runs[0] or any(r != runs[0] for r in runs):
        return FalsificationCheck(cid, "not_reproduced", "failed",
                                  "repeating the identical search did not return the identical root(s)", metrics, tol)
    return FalsificationCheck(cid, "not_reproduced", "passed",
                              f"{cfg.repeats} identical searches returned bit-identical roots", metrics, tol)


def check_independent_roots(root: Root, w: float, reference_roots: Sequence[float] | None, cfg: FalsificationConfig
                            ) -> FalsificationCheck:
    cid, tol = "X6_independent_roots", f"agree_tol={cfg.agree_tol} x w"
    if reference_roots is None:
        return FalsificationCheck(cid, "not_independently_reproduced", "skipped",
                                  "no independently computed root was supplied", {}, tol)
    near = _nearest(list(reference_roots), root.value)
    if near is None:
        return FalsificationCheck(cid, "not_independently_reproduced", "failed",
                                  "the independent calculation finds no root in the range",
                                  {"n_reference_roots": 0}, tol)
    dev = abs(near - root.value) / w
    m: dict[str, Metric] = {"n_reference_roots": len(list(reference_roots)), "deviation_over_w": dev}
    if dev > cfg.agree_tol:
        return FalsificationCheck(cid, "not_independently_reproduced", "failed",
                                  f"nearest independent root differs by {dev:.3g} x w", m, tol)
    return FalsificationCheck(cid, "not_independently_reproduced", "passed",
                              f"independent root agrees within {dev:.3g} x w", m, tol)


def check_perturbations(
    make_g: MakeG, base: Mapping[str, float], search_var: str, perturb_vars: Sequence[str], root: Root,
    lo: float, hi: float, n: int, xtol: float, cfg: FalsificationConfig,
) -> FalsificationCheck:
    cid, tol = "X5_perturbation", f"levels={cfg.perturbation_levels}, one input at a time, +/-"
    if root.beneficial_side == "unknown":
        return FalsificationCheck(cid, "perturbation_fragile", "inconclusive",
                                  "root has no determined beneficial side to preserve", {}, tol)
    skipped: list[str] = [v for v in perturb_vars if base[v] == 0.0]
    vars_ = [v for v in perturb_vars if v != search_var and base[v] != 0.0]

    def solve(over: dict[str, float]) -> tuple[bool | None, float | None]:
        try:
            sc = find_breakevens(make_g(over), lo, hi, n, xtol)
        except EcoBackfireError:
            return None, None
        good = [
            r for r in sc.roots
            if r.kind == "crossing" and r.converged and r.beneficial_side == root.beneficial_side
        ]
        near = _nearest([r.value for r in good], root.value)
        return near is not None, near

    per_level: dict[float, list[bool]] = {L: [] for L in cfg.perturbation_levels}
    n_invalid = 0
    lost: list[str] = []
    shift: dict[str, dict[int, float | None]] = {v: {} for v in vars_}
    for v in vars_:
        for L in cfg.perturbation_levels:
            for s in (-1, 1):
                ok, near = solve({v: base[v] * (1 + s * L)})
                if ok is None:
                    n_invalid += 1
                    continue
                per_level[L].append(ok)
                if not ok:
                    lost.append(f"{v}{'+' if s > 0 else '-'}{L:g}")
                if cfg.perturbation_levels[0] == L:
                    shift[v][s] = near
    frac: dict[str, Metric] = {}
    for L, res in per_level.items():
        frac[f"preserved_fraction@{L:g}"] = (sum(res) / len(res)) if res else None
        frac[f"n_valid@{L:g}"] = len(res)
    sens: dict[str, Metric] = {}
    L0 = cfg.perturbation_levels[0]
    for v in vars_:
        lo_x, hi_x = shift[v].get(-1), shift[v].get(1)
        sens[f"S[{v}]"] = (None if (lo_x is None or hi_x is None or root.value == 0.0)
                           else (hi_x - lo_x) / (2 * L0 * root.value))
    defined = {k: abs(val) for k, val in sens.items() if isinstance(val, float)}
    metrics: dict[str, Metric] = {**frac, **sens, "n_invalid": n_invalid, "lost": ",".join(lost),
                                  "skipped_zero_base": ",".join(skipped),
                                  "dominant_input": max(defined, key=lambda k: defined[k]) if defined else None}
    if not any(per_level.values()):
        return FalsificationCheck(cid, "perturbation_fragile", "inconclusive",
                                  "no perturbation could be evaluated (all invalid or none requested)", metrics, tol)
    first = per_level[L0]
    if first and sum(first) < len(first):
        return FalsificationCheck(cid, "perturbation_fragile", "failed",
                                  f"finding lost under a {L0:.0%} perturbation: {', '.join(lost)}", metrics, tol)
    if lost:
        return FalsificationCheck(cid, "perturbation_fragile", "limits_claim",
                                  f"survives {L0:.0%} but lost under larger perturbation(s): {', '.join(lost)}",
                                  metrics, tol)
    return FalsificationCheck(cid, "perturbation_fragile", "passed",
                              f"finding preserved in all {sum(len(r) for r in per_level.values())} valid "
                              "perturbations", metrics, tol)


def check_no_crossing(
    g: Callable[[float], float], lo: float, hi: float, n: int, xtol: float, cfg: FalsificationConfig
) -> FalsificationCheck:
    """Attempt to falsify a 'no break-even in [lo, hi]' verdict (X7)."""
    cid, tol = "X7_no_crossing_verdict", f"dense_factor={cfg.dense_factor}, near_zero_rel={cfg.near_zero_rel}"
    nd = (n - 1) * cfg.dense_factor + 1
    try:
        sc: BreakevenScan = find_breakevens(g, lo, hi, nd, xtol)
    except EcoBackfireError as exc:
        return FalsificationCheck(cid, "false_no_crossing", "failed", f"dense re-scan failed: {exc}", {}, tol)
    if sc.roots:
        return FalsificationCheck(cid, "false_no_crossing", "failed",
                                  f"dense re-scan finds {len(sc.roots)} break-even point(s) (first at "
                                  f"{sc.roots[0].value:.9g}) that the original grid missed",
                                  {"n_dense_roots": len(sc.roots), "dense_points": nd}, tol)
    vals = np.array([v for _, v in sc.scan])
    xs = np.array([x for x, _ in sc.scan])
    gscale = max(float(np.max(np.abs(vals))), 1e-300)
    a = np.abs(vals)
    interior = np.where((a[1:-1] <= a[:-2]) & (a[1:-1] <= a[2:]))[0] + 1
    if interior.size:
        j = int(interior[np.argmin(a[interior])])
        rel = float(a[j] / gscale)
        if rel < cfg.near_zero_rel:
            return FalsificationCheck(cid, "false_no_crossing", "inconclusive",
                                      f"|g| comes within {rel:.3g} of its scale at x={xs[j]:.9g} without changing "
                                      "sign: a touching (even-multiplicity) root cannot be excluded",
                                      {"min_abs_g_rel": rel, "at": float(xs[j]), "dense_points": nd}, tol)
    return FalsificationCheck(cid, "false_no_crossing", "passed",
                              f"no sign change on a {nd}-point re-scan and no near-zero interior minimum",
                              {"dense_points": nd}, tol)


# ------------------------------------------------------------------ orchestration


def _validate_search(root_value: float | None, lo: float, hi: float, n: int, xtol: float) -> None:
    if not (math.isfinite(lo) and math.isfinite(hi) and lo < hi):
        raise ConfigError(f"search range requires finite low < high, got [{lo}, {hi}]")
    if not isinstance(n, int) or n < 3:
        raise ConfigError("grid_points must be an integer >= 3")
    if not (math.isfinite(xtol) and xtol > 0):
        raise ConfigError("xtol must be positive and finite")
    if root_value is not None and not (lo <= root_value <= hi):
        raise ConfigError(f"root {root_value} lies outside the search range [{lo}, {hi}]")


def falsify_breakeven(
    g: Callable[[float], float],
    root: Root,
    lo: float,
    hi: float,
    *,
    grid_points: int,
    xtol: float,
    subject: str = "breakeven",
    config: FalsificationConfig | None = None,
    make_g: MakeG | None = None,
    base: Mapping[str, float] | None = None,
    search_var: str | None = None,
    perturb_vars: Sequence[str] | None = None,
    reference_roots: Sequence[float] | None = None,
    domain: tuple[float, float] | None = None,
) -> FalsificationReport:
    """Run X1-X6 against one candidate root. Never raises because a check fails; raises ConfigError only
    for an invalid configuration."""
    cfg = config or FalsificationConfig()
    cfg.validate()
    _validate_search(root.value, lo, hi, grid_points, xtol)
    if make_g is not None:
        if base is None or search_var is None:
            raise ConfigError("make_g requires base and search_var")
        missing = [v for v in (perturb_vars or []) if v not in base]
        if missing:
            raise ConfigError(f"perturb_vars not in base inputs: {missing}")
    checks = [
        check_crossing(g, root, lo, hi, xtol, cfg),
        check_numerical_stability(g, root, lo, hi, grid_points, xtol, cfg),
        check_boundary(g, root, lo, hi, grid_points, xtol, cfg, domain),
        check_reproducibility(g, root, lo, hi, grid_points, xtol, cfg),
        check_independent_roots(root, hi - lo, reference_roots, cfg),
    ]
    if make_g is not None and base is not None and search_var is not None and perturb_vars:
        checks.append(check_perturbations(make_g, base, search_var, list(perturb_vars), root, lo, hi,
                                          grid_points, xtol, cfg))
    else:
        checks.append(FalsificationCheck("X5_perturbation", "perturbation_fragile", "skipped",
                                         "no perturbation targets supplied", {}, ""))
    return FalsificationReport(subject, root.value, lo, hi, asdict(cfg), checks, dict(METRIC_DEFINITIONS))


def falsify_no_crossing(
    g: Callable[[float], float], lo: float, hi: float, *, grid_points: int, xtol: float,
    subject: str = "no-crossing", config: FalsificationConfig | None = None,
) -> FalsificationReport:
    cfg = config or FalsificationConfig()
    cfg.validate()
    _validate_search(None, lo, hi, grid_points, xtol)
    chk = check_no_crossing(g, lo, hi, grid_points, xtol, cfg)
    return FalsificationReport(subject, None, lo, hi, asdict(cfg), [chk], dict(METRIC_DEFINITIONS))
