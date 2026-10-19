"""Deterministic robustness analysis for a 1-D break-even candidate.  Pure numerics (numpy + the existing
``find_breakevens`` and falsification helpers); no pydantic, so it is testable anywhere.

Question asked of every candidate: *does the finding survive explicitly configured changes to its
assumptions?*  This module only MEASURES; it assigns no class (classification is a separate, later step)
and produces no probability, "confidence" or truth score.

What the analysis is, and is not
  Every perturbed parameter carries a ``basis``:
    evidence_backed   range comes from evidence records (``evidence_ids`` required; this module records the
                      ids but cannot verify them -- resolution against the evidence store is glue work);
    user_uncertainty  the user states this is their uncertainty range for the parameter;
    configured        a range chosen only to probe the model (e.g. +/-10%).  Nothing is claimed about how
                      plausible values inside it are.
  ``analysis_kind`` is ``configured_sensitivity_experiment`` if ANY parameter is ``configured`` (the
  conservative choice), otherwise ``user_or_evidence_ranged_sensitivity``.  Even the latter is a
  deterministic grid over a range, NOT an empirical uncertainty analysis: grid points carry no probability
  mass, so the preservation ratio is a share of grid points, never a probability that a real-world claim is
  true.  The module never invents a range: low/high are mandatory in every ``PerturbationSpec``.

Strategy implemented: ``linspace`` one-at-a-time (OAT).  For each parameter p (sorted by name) the values
are ``np.linspace(low, high, n_evals)`` (endpoints included), every other input stays at ``base``.  A
separate baseline case re-solves the unperturbed problem.  No randomness anywhere; no seed exists.

Conventions: g(x) < 0 means 'beneficial'.  The candidate is a root x* of g in [lo, hi] (width w =
hi - lo) with beneficial side ``root.beneficial_side``.  The optional operating point x_op is the value of
the searched variable at which the system currently runs.

Metric definitions (METRIC_DEFINITIONS is copied into every report)
  case status        preserved: the re-scan of [lo, hi] contains a converged, continuous crossing with the
                     same beneficial side as the candidate (the same rule as falsification X5).
                     lost: it does not (reason recorded).  invalid: g raised an EcoBackfireError (e.g. the
                     perturbed input leaves the model's physical domain); invalid cases are listed and
                     excluded from every ratio.
  n_cases/n_valid/   cases evaluated (excluding the baseline case) / preserved+lost / invalid.
  n_preserved/n_lost
  preservation_ratio n_preserved / n_valid (None if n_valid == 0).  A share of configured grid points.
  root_shift_rel     (x*_case - x*) / x*   (None if x* == 0).
  root_min/root_max  smallest / largest x*_case over preserved cases ('effect' = threshold location).
  effect_at_op_min/  min / max of g(x_op) over valid cases where g(x_op) was evaluable (the decision
  effect_at_op_max   metric at the operating point; needs operating_point).
  boundary_distance  |x*_case - x_op| / w for a preserved case.  min_boundary_distance is the minimum over
                     preserved cases (needs operating_point).  Small means the operating point sits close
                     to the break-even boundary in some configured case.
  operating_flip     sign(g_case(x_op)) differs from sign(g_baseline(x_op)): the decision at the
                     operating point reverses.  n_operating_flips counts valid cases with a flip.
  edge_margin        min(x*_case - lo, hi - x*_case) / w for a preserved case; min_edge_margin is the
                     minimum.  Small means the finding sits near the edge of the searched range.
  arc_elasticity     per parameter: ((x*(high) - x*(low)) / x*) / ((high - low) / baseline), from the two
                     endpoint cases; None if either endpoint is not preserved or x* == 0 or baseline == 0.
                     It is the mean log-slope of the threshold over the configured range.
  max_abs_root_shift_rel
                     per parameter: max |root_shift_rel| over its preserved cases.  dominant_parameter is
                     the one with the largest defined value (ties: first by name).
  smallest_losing_deviation
                     per parameter: the smallest |value - baseline| among its lost cases (None if none);
                     the relative form divides by |baseline| (None if baseline == 0).
  bisection_dev      |independent 200-step bisection root - x*_case| / w; max over preserved cases.
  independent        if ``reference_roots`` is supplied: for every valid case the independent roots are
                     compared; agreement iff (a) both paths find a root and the nearest independent root
                     is within agree_tol * w of x*_case, or (b) neither finds one.  Counts and the maximum
                     deviation are reported.
  baseline_reproduced the baseline case re-solves to a root within agree_tol * w of the candidate root
                     with the same beneficial side.
  deterministic      the entire evaluation was repeated ``repeats`` times and every case record was
                     identical (canonical JSON).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Literal

import numpy as np

from ecobackfire.core.hashing import canonical_json, sha256_text
from ecobackfire.discovery.falsification import _bisect
from ecobackfire.errors import ConfigError, EcoBackfireError
from ecobackfire.experiments.breakeven import Root, find_breakevens

Basis = Literal["evidence_backed", "user_uncertainty", "configured"]
CaseStatus = Literal["preserved", "lost", "invalid"]
AnalysisKind = Literal["configured_sensitivity_experiment", "user_or_evidence_ranged_sensitivity"]
MakeG = Callable[[Mapping[str, float]], Callable[[float], float]]
ReferenceRoots = Callable[[Mapping[str, float]], Sequence[float]]

BASES: tuple[str, ...] = ("evidence_backed", "user_uncertainty", "configured")
METHODS: tuple[str, ...] = ("linspace",)
MAX_EVALS_PER_PARAMETER = 1001

ANALYSIS_LABELS: dict[str, str] = {
    "configured_sensitivity_experiment": (
        "CONFIGURED SENSITIVITY EXPERIMENT: at least one parameter range was chosen to probe the model and is "
        "not backed by an uncertainty statement or evidence. The result says how the finding responds to these "
        "configured changes, not how uncertain the real quantity is."
    ),
    "user_or_evidence_ranged_sensitivity": (
        "RANGED SENSITIVITY ANALYSIS: every range is user-stated or evidence-backed, but the analysis is a "
        "deterministic grid, not a probabilistic uncertainty analysis; grid points carry no probability."
    ),
}

METRIC_DEFINITIONS: dict[str, str] = {
    "case_status": "preserved = converged continuous crossing with the same beneficial side in [lo, hi]; "
                   "lost = not; invalid = g raised an EcoBackfireError (excluded from ratios)",
    "preservation_ratio": "n_preserved / n_valid: a share of configured grid points, NOT a probability",
    "root_shift_rel": "(x*_case - x*) / x*",
    "boundary_distance": "|x*_case - x_op| / w, minimum over preserved cases",
    "operating_flip": "sign(g_case(x_op)) != sign(g_baseline(x_op))",
    "edge_margin": "min(x*_case - lo, hi - x*_case) / w, minimum over preserved cases",
    "arc_elasticity": "((x*(high) - x*(low)) / x*) / ((high - low) / baseline) from the endpoint cases",
    "max_abs_root_shift_rel": "max |root_shift_rel| over a parameter's preserved cases",
    "smallest_losing_deviation": "smallest |value - baseline| among a parameter's lost cases",
    "bisection_dev": "|200-step bisection root - x*_case| / w, maximum over preserved cases",
    "independent_agreement": "nearest independent root within agree_tol * w, or both paths find none",
    "baseline_reproduced": "baseline case re-solves to the candidate root (agree_tol * w), same side",
    "deterministic": "repeated full evaluation gave identical case records",
}

LIMITATIONS: tuple[str, ...] = (
    "One-at-a-time perturbation: interactions between parameters are not tested by this strategy.",
    "A deterministic grid over a configured range is not an uncertainty analysis; grid points carry no "
    "probability and preservation_ratio is not the probability that the finding is true.",
    "Only break-evens that the existing scan resolves on the configured grid are seen; sub-grid sign changes "
    "and even-multiplicity roots are invisible (see find_breakevens).",
    "Results are conditional on the model, its structural assumptions and the searched range; model-structure "
    "uncertainty is not covered by parameter perturbation.",
    "Unit handling here is an exact string comparison against caller-supplied expected units; conversion "
    "belongs to the unit layer.",
)


# ------------------------------------------------------------------ contract


@dataclass(frozen=True)
class PerturbationSpec:
    """How one parameter may be perturbed. Nothing is inferred: every field is explicit."""

    name: str
    unit: str
    baseline: float
    low: float
    high: float
    method: str = "linspace"
    n_evals: int = 5
    basis: str = "configured"
    evidence_ids: tuple[str, ...] = ()

    def validate(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ConfigError("perturbation name must be a non-empty string")
        n = self.name
        if not isinstance(self.unit, str) or not self.unit.strip():
            raise ConfigError(f"{n}: perturbation unit must be a non-empty string")
        for label, v in (("baseline", self.baseline), ("low", self.low), ("high", self.high)):
            if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
                raise ConfigError(f"{n}: {label} must be a finite number")
        if not self.low < self.high:
            raise ConfigError(f"{n}: requires low < high, got [{self.low}, {self.high}]")
        if not self.low <= self.baseline <= self.high:
            raise ConfigError(f"{n}: baseline {self.baseline} lies outside [{self.low}, {self.high}]")
        if self.method not in METHODS:
            raise ConfigError(f"{n}: unknown perturbation method {self.method!r}; supported: {list(METHODS)}")
        if isinstance(self.n_evals, bool) or not isinstance(self.n_evals, int):
            raise ConfigError(f"{n}: n_evals must be an integer")
        if not 2 <= self.n_evals <= MAX_EVALS_PER_PARAMETER:
            raise ConfigError(f"{n}: n_evals must be in [2, {MAX_EVALS_PER_PARAMETER}], got {self.n_evals}")
        if self.basis not in BASES:
            raise ConfigError(f"{n}: unknown basis {self.basis!r}; supported: {list(BASES)}")
        ids = list(self.evidence_ids)
        if any(not isinstance(i, str) or not i.strip() for i in ids) or len(set(ids)) != len(ids):
            raise ConfigError(f"{n}: evidence_ids must be unique non-empty strings")
        if self.basis == "evidence_backed" and not ids:
            raise ConfigError(f"{n}: basis 'evidence_backed' requires at least one evidence id")

    def values(self) -> list[float]:
        """The deterministic evaluation points (endpoints included)."""
        return [float(v) for v in np.linspace(self.low, self.high, self.n_evals)]


def relative_spec(
    name: str, unit: str, baseline: float, half_width: float, n_evals: int = 5, basis: str = "configured",
    evidence_ids: Sequence[str] = (),
) -> PerturbationSpec:
    """Convenience: low/high = baseline * (1 -/+ half_width). Requires baseline != 0 and 0 < half_width < 1."""
    if isinstance(baseline, bool) or not isinstance(baseline, int | float) or not math.isfinite(baseline):
        raise ConfigError(f"{name}: baseline must be a finite number")
    if baseline == 0.0:
        raise ConfigError(f"{name}: a relative range around a zero baseline is empty; give explicit low/high")
    if isinstance(half_width, bool) or not isinstance(half_width, int | float) \
            or not (math.isfinite(half_width) and 0 < half_width < 1):
        raise ConfigError(f"{name}: half_width must be in (0, 1)")
    a, b = baseline * (1 - half_width), baseline * (1 + half_width)
    return PerturbationSpec(name, unit, float(baseline), min(a, b), max(a, b), "linspace", n_evals, basis,
                            tuple(evidence_ids))


@dataclass(frozen=True)
class RobustnessConfig:
    """Numerical policy. Documented choices, recorded in the report; not derived from data."""

    agree_tol: float = 1e-8
    repeats: int = 2
    max_cases: int = 5000

    def validate(self) -> None:
        if not (math.isfinite(self.agree_tol) and 0 < self.agree_tol < 1):
            raise ConfigError("agree_tol must be in (0, 1)")
        if isinstance(self.repeats, bool) or not isinstance(self.repeats, int) or self.repeats < 2:
            raise ConfigError("repeats must be an integer >= 2")
        if isinstance(self.max_cases, bool) or not isinstance(self.max_cases, int) or self.max_cases < 1:
            raise ConfigError("max_cases must be a positive integer")


@dataclass(frozen=True)
class CaseResult:
    parameter: str
    value: float
    deviation: float  # value - baseline
    relative_deviation: float | None  # None if baseline == 0
    status: CaseStatus
    root: float | None
    root_shift_rel: float | None
    edge_margin: float | None
    boundary_distance: float | None
    g_at_op: float | None
    operating_flip: bool | None
    bisection_dev: float | None
    independent_dev: float | None
    independent_agrees: bool | None
    n_roots: int
    reason: str


@dataclass(frozen=True)
class ParameterSummary:
    name: str
    unit: str
    baseline: float
    low: float
    high: float
    method: str
    n_evals: int
    basis: str
    evidence_ids: tuple[str, ...]
    n_valid: int
    n_invalid: int
    n_preserved: int
    preservation_ratio: float | None
    max_abs_root_shift_rel: float | None
    arc_elasticity: float | None
    smallest_losing_deviation: float | None
    smallest_losing_relative_deviation: float | None


@dataclass(frozen=True)
class RobustnessReport:
    subject: str
    analysis_kind: str
    analysis_label: str
    strategy: str
    candidate_root: float
    candidate_side: str
    search_low: float
    search_high: float
    grid_points: int
    xtol: float
    operating_point: float | None
    config: dict[str, object]
    parameters: list[ParameterSummary]
    baseline_case: CaseResult
    cases: list[CaseResult]
    metrics: dict[str, object]
    failed_checks: list[str]
    metric_definitions: dict[str, str]
    limitations: list[str]
    interpretation: str = (
        "Preservation ratios and distances describe the configured experiment only. They are not probabilities "
        "and do not say whether the real-world claim is true."
    )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def sha256(self) -> str:
        return sha256_text(canonical_json(self.to_dict()))


# ------------------------------------------------------------------ helpers


def _finite(v: float | None) -> float | None:
    return None if v is None or not math.isfinite(v) else float(v)


def _sgn(v: float) -> int:
    return (v > 0) - (v < 0)


def analysis_kind_of(perturbations: Sequence[PerturbationSpec]) -> str:
    if any(p.basis == "configured" for p in perturbations) or not perturbations:
        return "configured_sensitivity_experiment"
    return "user_or_evidence_ranged_sensitivity"


def validate_perturbations(
    perturbations: Sequence[PerturbationSpec], base: Mapping[str, float], search_var: str,
    expected_units: Mapping[str, str] | None, config: RobustnessConfig,
) -> list[PerturbationSpec]:
    """Return the specs sorted by name, or raise ConfigError. Checks structure, names, units, baselines, size."""
    if not perturbations:
        raise ConfigError("at least one perturbation is required")
    names = [p.name for p in perturbations]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ConfigError(f"duplicate perturbation(s): {dup}")
    for p in perturbations:
        p.validate()
        if p.name == search_var:
            raise ConfigError(f"{p.name}: the searched variable cannot be perturbed")
        if p.name not in base:
            raise ConfigError(f"{p.name}: not an input of the base configuration")
        if not math.isclose(p.baseline, base[p.name], rel_tol=1e-12, abs_tol=0.0):
            raise ConfigError(f"{p.name}: perturbation baseline {p.baseline} differs from the base input "
                              f"{base[p.name]}")
    if expected_units is not None:
        for p in perturbations:
            if p.name not in expected_units:
                raise ConfigError(f"{p.name}: no expected unit supplied; cannot check the unit")
            if p.unit != expected_units[p.name]:
                raise ConfigError(f"{p.name}: unit mismatch: perturbation uses {p.unit!r} but the model input is "
                                  f"{expected_units[p.name]!r}")
    total = sum(p.n_evals for p in perturbations)
    if total > config.max_cases:
        raise ConfigError(f"{total} perturbation cases exceed max_cases={config.max_cases}; the search is bounded")
    return sorted(perturbations, key=lambda p: p.name)


def _solve_case(
    make_g: MakeG, over: Mapping[str, float], root: Root, lo: float, hi: float, n: int, xtol: float,
    cfg: RobustnessConfig, x_op: float | None, g_base_op_sign: int | None, parameter: str, value: float,
    baseline: float, reference_roots: ReferenceRoots | None,
) -> CaseResult:
    w = hi - lo
    dev = value - baseline
    rel = None if baseline == 0.0 else dev / baseline

    def invalid(reason: str) -> CaseResult:
        return CaseResult(parameter, value, dev, rel, "invalid", None, None, None, None, None, None, None, None,
                          None, 0, reason)

    try:
        g = make_g(over)
        scan = find_breakevens(g, lo, hi, n, xtol)
    except EcoBackfireError as exc:
        return invalid(f"{type(exc).__name__}: {exc}")
    good = [r for r in scan.roots if r.kind == "crossing" and r.converged and r.beneficial_side == root.beneficial_side]
    g_op: float | None = None
    flip: bool | None = None
    if x_op is not None:
        try:
            g_op = float(g(x_op))
            if not math.isfinite(g_op):
                g_op = None
        except EcoBackfireError:
            g_op = None
        if g_op is not None and g_base_op_sign is not None:
            flip = _sgn(g_op) != g_base_op_sign
    ind_dev: float | None = None
    ind_ok: bool | None = None
    own = [r.value for r in good]
    near = min(own, key=lambda r: abs(r - root.value)) if own else None
    if reference_roots is not None:
        refs: list[float] | None
        try:
            refs = [float(v) for v in reference_roots(over)]
        except EcoBackfireError:
            refs = None  # independent path could not evaluate this case: no comparison recorded
        if refs is not None:
            if near is None:
                ind_ok = not refs  # neither path finds the candidate-type root
            elif not refs:
                ind_ok = False
            else:
                nr = min(refs, key=lambda r: abs(r - near))
                ind_dev = abs(nr - near) / w
                ind_ok = ind_dev <= cfg.agree_tol
    if near is None:
        if scan.roots:
            reason = "roots exist but none is a converged continuous crossing with the same beneficial side"
        else:
            reason = scan.classification
        return CaseResult(parameter, value, dev, rel, "lost", None, None, None, None, _finite(g_op), flip, None,
                          _finite(ind_dev), ind_ok, len(scan.roots), reason)
    chosen = next(r for r in good if r.value == near)
    bis: float | None = None
    if chosen.bracket_low is not None and chosen.bracket_high is not None:
        xb = _bisect(g, chosen.bracket_low, chosen.bracket_high)
        bis = None if xb is None else abs(xb - near) / w
    shift = None if root.value == 0.0 else (near - root.value) / root.value
    dist = None if x_op is None else abs(near - x_op) / w
    return CaseResult(parameter, value, dev, rel, "preserved", near, _finite(shift),
                      min(near - lo, hi - near) / w, _finite(dist), _finite(g_op), flip, _finite(bis),
                      _finite(ind_dev), ind_ok, len(scan.roots), "")


def _evaluate(
    make_g: MakeG, specs: list[PerturbationSpec], root: Root, lo: float, hi: float, n: int, xtol: float,
    cfg: RobustnessConfig, x_op: float | None, reference_roots: ReferenceRoots | None,
) -> tuple[CaseResult, list[CaseResult]]:
    g_base_sign: int | None = None
    if x_op is not None:
        try:
            g_base_sign = _sgn(float(make_g({})(x_op)))
        except EcoBackfireError:
            g_base_sign = None
    base_case = _solve_case(make_g, {}, root, lo, hi, n, xtol, cfg, x_op, g_base_sign, "(baseline)", 0.0, 0.0,
                            reference_roots)
    cases: list[CaseResult] = []
    for p in specs:
        for v in p.values():
            cases.append(_solve_case(make_g, {p.name: v}, root, lo, hi, n, xtol, cfg, x_op, g_base_sign, p.name, v,
                                     p.baseline, reference_roots))
    return base_case, cases


def _summarize(p: PerturbationSpec, cases: list[CaseResult], root: Root) -> ParameterSummary:
    mine = [c for c in cases if c.parameter == p.name]
    valid = [c for c in mine if c.status != "invalid"]
    kept = [c for c in mine if c.status == "preserved"]
    lost = [c for c in mine if c.status == "lost"]
    shifts = [abs(c.root_shift_rel) for c in kept if c.root_shift_rel is not None]
    arc: float | None = None
    lo_c, hi_c = (mine[0], mine[-1]) if mine else (None, None)
    if (lo_c and hi_c and lo_c.root is not None and hi_c.root is not None and root.value != 0.0
            and p.baseline != 0.0):
        arc = _finite(((hi_c.root - lo_c.root) / root.value) / ((p.high - p.low) / p.baseline))
    sl = min((abs(c.deviation) for c in lost), default=None)
    return ParameterSummary(
        p.name, p.unit, p.baseline, p.low, p.high, p.method, p.n_evals, p.basis, tuple(p.evidence_ids),
        len(valid), len(mine) - len(valid), len(kept), (len(kept) / len(valid)) if valid else None,
        max(shifts) if shifts else None, arc, sl, None if sl is None or p.baseline == 0.0 else sl / abs(p.baseline),
    )


# ------------------------------------------------------------------ orchestration


def analyze_robustness(
    make_g: MakeG,
    base: Mapping[str, float],
    search_var: str,
    root: Root,
    lo: float,
    hi: float,
    *,
    grid_points: int,
    xtol: float,
    perturbations: Sequence[PerturbationSpec],
    config: RobustnessConfig | None = None,
    operating_point: float | None = None,
    reference_roots: ReferenceRoots | None = None,
    expected_units: Mapping[str, str] | None = None,
    subject: str = "breakeven",
) -> RobustnessReport:
    """One-at-a-time ``linspace`` robustness analysis of one candidate root.

    ``make_g(over)`` must return the decision function g(x) with the inputs in ``over`` replaced (the
    existing model-backed ``make_g`` or a pure function). Raises ConfigError only for invalid configuration;
    a candidate that does not survive is a RESULT, never an exception.
    """
    cfg = config or RobustnessConfig()
    cfg.validate()
    if not (math.isfinite(lo) and math.isfinite(hi) and lo < hi):
        raise ConfigError(f"search range requires finite low < high, got [{lo}, {hi}]")
    if isinstance(grid_points, bool) or not isinstance(grid_points, int) or grid_points < 3:
        raise ConfigError("grid_points must be an integer >= 3")
    if not (math.isfinite(xtol) and xtol > 0):
        raise ConfigError("xtol must be positive and finite")
    if not lo <= root.value <= hi:
        raise ConfigError(f"root {root.value} lies outside the search range [{lo}, {hi}]")
    if root.beneficial_side == "unknown":
        raise ConfigError("the candidate root has no determined beneficial side to preserve")
    if operating_point is not None and not (math.isfinite(operating_point) and lo <= operating_point <= hi):
        raise ConfigError(f"operating_point must be finite and inside [{lo}, {hi}]")
    specs = validate_perturbations(perturbations, base, search_var, expected_units, cfg)

    runs = [_evaluate(make_g, specs, root, lo, hi, grid_points, xtol, cfg, operating_point, reference_roots)
            for _ in range(cfg.repeats)]
    keys = {canonical_json([asdict(runs[i][0])] + [asdict(c) for c in runs[i][1]]) for i in range(cfg.repeats)}
    deterministic = len(keys) == 1
    base_case, cases = runs[0]

    w = hi - lo
    valid = [c for c in cases if c.status != "invalid"]
    kept = [c for c in cases if c.status == "preserved"]
    roots_kept = [c.root for c in kept if c.root is not None]
    g_ops = [c.g_at_op for c in valid if c.g_at_op is not None]
    dists = [c.boundary_distance for c in kept if c.boundary_distance is not None]
    margins = [c.edge_margin for c in kept if c.edge_margin is not None]
    bis = [c.bisection_dev for c in kept if c.bisection_dev is not None]
    ind = [c for c in valid if c.independent_agrees is not None]
    ind_devs = [c.independent_dev for c in ind if c.independent_dev is not None]
    baseline_reproduced = (
        base_case.status == "preserved" and base_case.root is not None
        and abs(base_case.root - root.value) / w <= cfg.agree_tol
    )
    summaries = [_summarize(p, cases, root) for p in specs]
    defined = [(s.max_abs_root_shift_rel, s.name) for s in summaries if s.max_abs_root_shift_rel is not None]
    dominant = sorted(defined, key=lambda t: (-t[0], t[1]))[0][1] if defined else None  # ties: first by name

    failed: list[str] = []
    if not baseline_reproduced:
        failed.append("baseline_not_reproduced: the unperturbed case does not re-solve to the candidate root")
    for s in summaries:
        n_lost = s.n_valid - s.n_preserved
        if n_lost:
            failed.append(f"lost_under_perturbation[{s.name}]: finding lost in {n_lost} of {s.n_valid} valid cases")
        if s.n_invalid:
            failed.append(f"invalid_cases[{s.name}]: {s.n_invalid} of {s.n_valid + s.n_invalid} cases left the "
                          "model's valid domain and were excluded")
    n_disagree = sum(1 for c in ind if c.independent_agrees is False)
    if n_disagree:
        failed.append(f"independent_disagreement: {n_disagree} of {len(ind)} cases disagree with the independent "
                      "calculation")
    if bis and max(bis) > cfg.agree_tol:
        failed.append(f"bisection_disagreement: maximum deviation {max(bis):.3g} x w exceeds {cfg.agree_tol}")
    if not deterministic:
        failed.append("nondeterministic: repeated evaluation produced different case records")

    metrics: dict[str, object] = {
        "n_cases": len(cases),
        "n_valid": len(valid),
        "n_invalid": len(cases) - len(valid),
        "n_preserved": len(kept),
        "n_lost": len(valid) - len(kept),
        "preservation_ratio": (len(kept) / len(valid)) if valid else None,
        "root_min": min(roots_kept) if roots_kept else None,
        "root_max": max(roots_kept) if roots_kept else None,
        "effect_at_op_min": min(g_ops) if g_ops else None,
        "effect_at_op_max": max(g_ops) if g_ops else None,
        "min_boundary_distance": min(dists) if dists else None,
        "min_edge_margin": min(margins) if margins else None,
        "n_operating_flips": sum(1 for c in valid if c.operating_flip is True),
        "max_bisection_dev": max(bis) if bis else None,
        "n_independent_compared": len(ind),
        "n_independent_agree": len(ind) - n_disagree,
        "max_independent_dev": max(ind_devs) if ind_devs else None,
        "baseline_reproduced": baseline_reproduced,
        "deterministic": deterministic,
        "repeats": cfg.repeats,
        "dominant_parameter": dominant,
        "n_parameters": len(specs),
    }
    kind = analysis_kind_of(specs)
    return RobustnessReport(
        subject=subject, analysis_kind=kind, analysis_label=ANALYSIS_LABELS[kind], strategy="oat_linspace",
        candidate_root=root.value, candidate_side=root.beneficial_side, search_low=lo, search_high=hi,
        grid_points=grid_points, xtol=xtol, operating_point=operating_point, config=asdict(cfg),
        parameters=summaries, baseline_case=base_case, cases=cases, metrics=metrics, failed_checks=failed,
        metric_definitions=dict(METRIC_DEFINITIONS), limitations=list(LIMITATIONS),
    )
