"""Verification and falsification checks for the efficiency_rebound model.

Each check returns a CheckResult. Failed and skipped checks are recorded, never discarded.
Independent references (analytical + Decimal) live in ecobackfire.verification.reference and do
not import the production model or runner.
"""

from __future__ import annotations

import itertools
import json
import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ecobackfire.contracts.common import IntervalUncertainty, ProvenanceClass
from ecobackfire.contracts.experiment import ExperimentConfiguration, ExperimentResult
from ecobackfire.contracts.verification import CheckResult, CheckStatus
from ecobackfire.core.engine import prepare_inputs
from ecobackfire.core.model import Model
from ecobackfire.errors import EcoBackfireError, NumericalError, PhysicalConstraintError
from ecobackfire.experiments.breakeven import BreakevenScan, find_breakevens
from ecobackfire.experiments.runner import make_g
from ecobackfire.units import to_canonical
from ecobackfire.verification.reference import rebound_analytical as ana
from ecobackfire.verification.reference import rebound_reference as ref

SUITE_VERSION = "1.0.0"


@dataclass
class Ctx:
    config: ExperimentConfiguration
    result: ExperimentResult
    model: Model
    opts: dict[str, str]
    base: dict[str, float]  # canonical units
    required: list[str]
    benchmarks_dir: Path | None
    #: input -> evidence id verified against a dataset registry by the caller (empty without evidence requests)
    verified_evidence: dict[str, str] = field(default_factory=dict)

    @property
    def metric(self):  # type: ignore[no-untyped-def]
        for m in self.model.spec.decision_metrics:
            if m.id == self.config.decision_metric_ref:
                return m
        raise KeyError(self.config.decision_metric_ref)

    @property
    def rtol(self) -> float:
        return self.config.verification.agreement_rtol

    def conv(self, vid: str, value_in_binding_unit: float) -> float:
        b = self.config.bindings[vid]
        return to_canonical(value_in_binding_unit, b.unit, self.model.spec.input_by_id(vid).canonical_unit, what=vid)

    def scan(
        self,
        var: str,
        lo: float,
        hi: float,
        n: int,
        xtol: float,
        base: Mapping[str, float] | None = None,
        opts: Mapping[str, str] | None = None,
    ) -> BreakevenScan:
        g = make_g(self.model, opts or self.opts, self.metric, base or self.base, var)
        return find_breakevens(g, lo, hi, n, xtol)


def mk(
    check_id: str,
    family: str,
    status: CheckStatus,
    summary: str,
    details: dict[str, object] | None = None,
    tol: str | None = None,
    required: bool = True,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        family=family,
        required=required,
        status=status,
        summary=summary,
        details=details or {},
        tolerance_used=tol,
    )


def _close(a: float, b: float, rtol: float, scale: float = 0.0) -> bool:
    return abs(a - b) <= rtol * max(abs(b), abs(scale), 1e-300)


def _root_close(a: float, b: float, rtol: float) -> bool:
    return abs(a - b) <= rtol * max(1.0, abs(b))


# --------------------------------------------------------------------------- V1


def check_closed_form(c: Ctx) -> CheckResult:
    cid, fam = "V1_closed_form_vs_numeric", "closed_form"
    be = c.result.breakeven
    if c.config.kind != "breakeven" or be is None or c.config.search is None:
        return mk(cid, fam, "not_applicable", "no break-even search in this experiment")
    var = be.variable_id
    if var not in ("epsilon", "f"):
        return mk(cid, fam, "not_applicable", f"no closed form implemented for variable {var!r}")
    lo, hi = be.search_low, be.search_high
    margin = 1e-9 * (hi - lo)
    try:
        pred_all = (ana.predicted_epsilon_roots if var == "epsilon" else ana.predicted_f_roots)(c.base, c.opts)
    except ValueError as exc:
        return mk(cid, fam, "inconclusive", f"closed form undefined for these inputs: {exc}")
    pred = [p for p in pred_all if lo + margin <= p <= hi - margin]
    num = [r.value for r in be.roots if lo + margin <= r.value <= hi - margin]
    ok = len(pred) == len(num) and all(
        _root_close(a, b, c.rtol) for a, b in zip(sorted(num), sorted(pred), strict=True)
    )
    det = {
        "variable": var,
        "closed_form_roots": pred,
        "numeric_roots": num,
        "max_abs_diff": max((abs(a - b) for a, b in zip(sorted(num), sorted(pred), strict=False)), default=0.0),
    }
    return mk(
        cid,
        fam,
        "passed" if ok else "failed",
        "numeric break-even agrees with closed form" if ok else "numeric break-even DISAGREES with closed form",
        det,
        f"rtol={c.rtol:g} (abs floor via max(1,|x|))",
    )


# --------------------------------------------------------------------------- V2


def _ref_dC_close(c: Ctx, vals: Mapping[str, float], prod_dC: float) -> tuple[bool, float]:
    r = ref.evaluate(vals, c.opts)
    scale = float(max(abs(r["C0"]), abs(r["C1"])))
    err = abs(prod_dC - float(r["dC"]))
    return err <= c.rtol * max(scale, 1e-300), err / max(scale, 1e-300)


def check_decimal_recompute(c: Ctx) -> CheckResult:
    cid, fam = "V2_independent_decimal_recompute", "independent_recomputation"
    mismatches: list[str] = []
    max_rel = 0.0
    n_points = 0
    n_roots = 0

    def cmp_outputs(label: str, vals: Mapping[str, float], out: Mapping[str, float]) -> None:
        nonlocal max_rel, n_points
        r = ref.evaluate(vals, c.opts)
        scale = float(max(abs(r["C0"]), abs(r["C1"])))
        for k in ("E1", "service_ratio", "C0", "C1", "dC"):
            n_points += 1
            sc = scale if k == "dC" else 0.0
            err = abs(out[k] - float(r[k])) / max(abs(float(r[k])), sc, 1e-300)
            max_rel = max(max_rel, err)
            if err > c.rtol:
                mismatches.append(f"{label}:{k} rel_err={err:.3g}")

    try:
        if c.config.kind == "compare":
            for sc_res in c.result.compare or []:
                vals = dict(c.base)
                for vid, v in sc_res.overrides.items():
                    vals[vid] = c.conv(vid, v)
                out = c.model.evaluate(vals, c.opts)
                cmp_outputs(f"scenario:{sc_res.name}", vals, out)
        if c.config.kind in ("breakeven", "oat_sweep") and c.result.breakeven is not None:
            be = c.result.breakeven
            var = be.variable_id
            stride = max(1, len(be.scan) // 30)
            for x, gval in be.scan[::stride]:
                vals = dict(c.base)
                vals[var] = x
                ok, rel = _ref_dC_close(c, vals, gval)
                n_points += 1
                max_rel = max(max_rel, rel)
                if not ok:
                    mismatches.append(f"scan x={x:.6g}: rel_err={rel:.3g}")
            ref_roots = ref.breakevens(c.base, c.opts, var, be.search_low, be.search_high, be.grid_points)
            n_roots = len(ref_roots)
            prod = [r.value for r in be.roots]
            if len(ref_roots) != len(prod):
                mismatches.append(f"root count: production={len(prod)} decimal_reference={len(ref_roots)}")
            else:
                for a, b in zip(prod, ref_roots, strict=True):
                    if not _root_close(a, float(b), c.rtol):
                        mismatches.append(f"root production={a!r} reference={float(b)!r}")
        if c.config.kind == "oat_sweep" and c.result.sensitivity is not None:
            sens = c.result.sensitivity
            assert c.config.oat is not None
            for row in sens.rows:
                for end, tval in (("low", row.target_at_low), ("high", row.target_at_high)):
                    vals = dict(c.base)
                    vals[row.variable_id] = c.conv(row.variable_id, row.low_value if end == "low" else row.high_value)
                    if sens.target == "delta_emissions":
                        assert tval is not None
                        ok, rel = _ref_dC_close(c, vals, tval)
                        n_points += 1
                        max_rel = max(max_rel, rel)
                        if not ok:
                            mismatches.append(f"oat {row.variable_id}/{end}: rel_err={rel:.3g}")
                    else:
                        assert c.config.search is not None
                        s = c.config.search
                        rr = ref.breakevens(
                            vals,
                            c.opts,
                            s.variable_id,
                            c.conv(s.variable_id, s.low),
                            c.conv(s.variable_id, s.high),
                            s.grid_points,
                        )
                        n_roots += 1
                        if (tval is None) != (len(rr) == 0) or (
                            tval is not None and not _root_close(tval, float(rr[0]), c.rtol)
                        ):
                            mismatches.append(f"oat {row.variable_id}/{end}: breakeven {tval} vs {rr[:1]}")
    except EcoBackfireError as exc:
        return mk(cid, fam, "failed", f"production model raised while recomputing: {exc}")
    ok = not mismatches
    return mk(
        cid,
        fam,
        "passed" if ok else "failed",
        f"production matches 60-digit Decimal reference ({n_points} comparisons, {n_roots} reference roots)"
        if ok
        else f"{len(mismatches)} mismatch(es) vs independent Decimal reference",
        {"max_relative_error": max_rel, "mismatches": mismatches[:20]},
        f"rtol={c.rtol:g}",
    )


# --------------------------------------------------------------------------- V3


def check_invariants(c: Ctx) -> CheckResult:
    cid, fam = "V3_invariants", "invariants"
    failures: list[str] = []
    base, opts, ev = c.base, c.opts, c.model.evaluate
    b = opts["boundary_variant"]
    em0e, em1e = ana.embodied_effective(b, base["EM0"], base["EM1"])
    try:
        # eta = 0 -> no energy change; dC = (EM1e-EM0e)/L
        v = dict(base, eta=0.0)
        o = ev(v, opts)
        if not _close(o["E1"], v["E0"], 1e-12):
            failures.append("eta=0 should give E1==E0")
        if not _close(o["dC"], (em1e - em0e) / v["L"], 1e-12, scale=o["C0"]):
            failures.append("eta=0 should give dC==(EM1e-EM0e)/L")
        # eps = 0 -> E1 = (1-eta) E0
        v = dict(base, epsilon=0.0)
        o = ev(v, opts)
        if not _close(o["E1"], (1 - v["eta"]) * v["E0"], 1e-12):
            failures.append("epsilon=0 should give E1==(1-eta)*E0")
        if not _close(o["service_ratio"], 1.0, 1e-12):
            failures.append("epsilon=0 should give service_ratio==1")
        # monotone non-decreasing in epsilon
        prev = -math.inf
        for i in range(61):
            x = 3.0 * i / 60
            d = ev(dict(base, epsilon=x), opts)["dC"]
            if d < prev - 1e-9 * max(1.0, abs(prev)):
                failures.append(f"dC decreased between epsilon grid points near {x:.3g}")
                break
            prev = d
        # scaling: E0 -> kE0 and EM -> kEM scales dC by k
        k = 3.7
        d0 = ev(base, opts)["dC"]
        d1 = ev(dict(base, E0=k * base["E0"], EM0=k * base["EM0"], EM1=k * base["EM1"]), opts)["dC"]
        if not _close(d1, k * d0, 1e-10, scale=abs(k * d0)):
            failures.append("dC does not scale linearly with (E0, EM0, EM1)")
        # linearity in EM1: d(dC)/d(EM1) = 1/L (B1,B2) or 0 (B3)
        delta = 123.0
        dd = ev(dict(base, EM1=base["EM1"] + delta), opts)["dC"] - d0
        expect = 0.0 if b == "B3" else delta / base["L"]
        if abs(dd - expect) > 1e-9 * max(1.0, abs(expect)):
            failures.append("dC is not linear in EM1 with slope 1/L (0 under B3)")
        # unit invariance: same physical inputs in other units must give the same canonical inputs
        alt = dict(c.config.bindings)
        conv_alt = {
            "E0": (3.6, "MJ/yr"),
            "f": (1000.0, "gCO2e/kWh"),
            "EM0": (1e-3, "tCO2e"),
            "EM1": (1e-3, "tCO2e"),
            "L": (12.0, "month"),
        }
        for vid, (mult, unit) in conv_alt.items():
            if vid in alt and alt[vid].value is not None:
                # Express the SAME physical quantity in the alternative unit. Built from the canonical value so it
                # is right whatever unit the binding itself uses (e.g. an evidence record in gCO2e/kWh).
                alt[vid] = alt[vid].model_copy(update={"value": c.base[vid] * mult, "unit": unit})
        prepared = prepare_inputs(c.model.spec, alt, c.required, verified_evidence=c.verified_evidence)
        for vid, val in c.base.items():
            if not _close(prepared.values[vid], val, 1e-12):
                failures.append(f"unit conversion changed {vid}: {prepared.values[vid]!r} vs {val!r}")
    except EcoBackfireError as exc:
        failures.append(f"model raised: {exc}")
    ok = not failures
    return mk(
        cid,
        fam,
        "passed" if ok else "failed",
        "all invariants hold" if ok else f"{len(failures)} invariant violation(s)",
        {"violations": failures},
        "rtol 1e-12..1e-9",
    )


# --------------------------------------------------------------------------- V4


def _find_benchmarks(explicit: Path | None) -> Path | None:
    cands = []
    if explicit:
        cands.append(explicit)
    env = os.environ.get("ECOBACKFIRE_BENCHMARKS")
    if env:
        cands.append(Path(env))
    cands.append(Path(__file__).resolve().parents[3] / "benchmarks")
    cands.append(Path.cwd() / "benchmarks")
    for p in cands:
        if (p / "rebound" / "cases.json").is_file():
            return p
    return None


def run_benchmarks(model: Model, bdir: Path) -> tuple[list[str], list[str]]:
    doc = json.loads((bdir / "rebound" / "cases.json").read_text(encoding="utf-8"))
    failures: list[str] = []
    ran: list[str] = []
    for case in doc["cases"]:
        ran.append(case["id"])
        opts, inputs = case["options"], case["inputs"]
        rtol = case["rtol"]
        try:
            if case["kind"] == "evaluate":
                out = model.evaluate(inputs, opts)
                for k, want in case["expected"].items():
                    if abs(out[k] - want) > rtol * abs(want) + case.get("atol", 0.0):
                        failures.append(f"{case['id']}: {k}={out[k]!r}, expected {want!r}")
            else:
                s = case["search"]
                sc = find_breakevens(
                    make_g(model, opts, _metric(model), inputs, s["variable_id"]),
                    s["low"],
                    s["high"],
                    s["grid_points"],
                    s["xtol"],
                )
                exp = case["expected"]
                if sc.classification != exp["classification"]:
                    failures.append(
                        f"{case['id']}: classification {sc.classification}, expected {exp['classification']}"
                    )
                got = [r.value for r in sc.roots]
                if len(got) != len(exp["roots"]) or any(
                    abs(a - b) > rtol * max(1.0, abs(b)) for a, b in zip(got, exp["roots"], strict=False)
                ):
                    failures.append(f"{case['id']}: roots {got}, expected {exp['roots']}")
        except EcoBackfireError as exc:
            failures.append(f"{case['id']}: raised {type(exc).__name__}: {exc}")
    return ran, failures


def _metric(model: Model):  # type: ignore[no-untyped-def]
    return model.spec.decision_metrics[0]


def check_benchmarks(c: Ctx) -> CheckResult:
    cid, fam = "V4_known_answer_benchmarks", "benchmarks"
    bdir = _find_benchmarks(c.benchmarks_dir)
    if bdir is None:
        return mk(cid, fam, "skipped", "benchmarks/rebound/cases.json not found (set ECOBACKFIRE_BENCHMARKS)")
    ran, failures = run_benchmarks(c.model, bdir)
    ok = not failures
    return mk(
        cid,
        fam,
        "passed" if ok else "failed",
        f"{len(ran)} known-answer cases reproduced" if ok else f"{len(failures)} benchmark failure(s)",
        {"cases": ran, "failures": failures},
        "per-case rtol (1e-9 roots, 1e-12 pointwise)",
    )


# --------------------------------------------------------------------------- Falsification


def _need_roots(c: Ctx, cid: str, fam: str) -> CheckResult | None:
    be = c.result.breakeven
    if c.config.kind != "breakeven" or be is None:
        return mk(cid, fam, "not_applicable", "no break-even search in this experiment")
    if not be.roots:
        return mk(cid, fam, "not_applicable", "no break-even found, so there is no claim to falsify")
    return None


def check_threshold_perturbation(c: Ctx) -> CheckResult:
    cid, fam = "F1_threshold_perturbation", "falsification_perturbation"
    early = _need_roots(c, cid, fam)
    if early:
        return early
    be = c.result.breakeven
    assert be is not None
    g = make_g(c.model, c.opts, c.metric, c.base, be.variable_id)
    problems: list[str] = []
    tested = 0
    for root in be.roots:
        for rel in (c.config.verification.perturbation_rel, 1e-6):
            d = max(rel * abs(root.value), 1e-9)
            for side, x in (("below", root.value - d), ("above", root.value + d)):
                if not (be.search_low <= x <= be.search_high):
                    continue
                tested += 1
                gv = g(x)
                expect_negative = root.beneficial_side == side
                if root.beneficial_side == "unknown":
                    continue
                if (gv < 0) != expect_negative or gv == 0.0:
                    problems.append(
                        f"root {root.value:.9g}: g({x:.9g})={gv:.3g} on the '{side}' side contradicts "
                        f"beneficial_side={root.beneficial_side}"
                    )
    ok = not problems and tested > 0
    return mk(
        cid,
        fam,
        "passed" if ok else ("failed" if problems else "inconclusive"),
        f"decision flips across each reported threshold ({tested} perturbed points)"
        if ok
        else (
            "claimed threshold contradicted by perturbation"
            if problems
            else "no perturbation point inside the search range"
        ),
        {"problems": problems, "perturbations_rel": [c.config.verification.perturbation_rel, 1e-6]},
    )


def check_counterexample_search(c: Ctx) -> CheckResult:
    cid, fam = "F2_counterexample_search", "falsification_counterexample"
    early = _need_roots(c, cid, fam)
    if early:
        return early
    be = c.result.breakeven
    assert be is not None
    n = c.config.verification.counterexample_points
    g = make_g(c.model, c.opts, c.metric, c.base, be.variable_id)
    # Dense re-sampling on a DIFFERENT grid (offset half a cell) than the original scan.
    step = (be.search_high - be.search_low) / n
    xs = [be.search_low + (i + 0.5) * step for i in range(n)]
    gs = [g(x) for x in xs]
    changes = [
        (xs[i], xs[i + 1]) for i in range(n - 1) if gs[i] != 0 and gs[i + 1] != 0 and ((gs[i] < 0) != (gs[i + 1] < 0))
    ]
    crossing_roots = [r.value for r in be.roots if r.kind != "grid_zero"]
    unexplained = [ch for ch in changes if not any(ch[0] <= r <= ch[1] for r in crossing_roots)]
    # also every reported (non-grid-zero) root must be re-found as a sign change or lie within a half cell of the edge
    missed = [
        r
        for r in crossing_roots
        if not any(ch[0] <= r <= ch[1] for ch in changes) and (be.search_low + step / 2 < r < be.search_high - step / 2)
    ]
    ok = not unexplained and not missed
    return mk(
        cid,
        fam,
        "passed" if ok else "failed",
        f"dense independent sampling ({n} points) found no break-even outside the reported set"
        if ok
        else "dense sampling found sign changes not explained by the reported break-evens (or lost reported ones)",
        {"sign_changes": changes[:10], "unexplained": unexplained[:10], "missed_roots": missed},
        f"{n} points, offset grid",
    )


def _first_root_or_none(sc: BreakevenScan) -> float | None:
    return sc.roots[0].value if sc.roots else None


def _alt_scan(c: Ctx, opts: dict[str, str], base: dict[str, float]) -> BreakevenScan:
    be = c.result.breakeven
    assert be is not None and c.config.search is not None
    return c.scan(
        be.variable_id, be.search_low, be.search_high, be.grid_points, c.config.search.xtol, base=base, opts=opts
    )


def check_alternative_demand_form(c: Ctx) -> CheckResult:
    cid, fam = "F3_alternative_demand_form", "model_form"
    early = _need_roots(c, cid, fam)
    if early:
        return early
    be = c.result.breakeven
    assert be is not None
    rows: list[dict[str, object]] = []
    mismatch: list[str] = []
    try:
        if c.opts["demand_form"] == "constant_elasticity":
            smax_list = c.config.verification.alternative_s_max
            for s in smax_list:
                opts = dict(c.opts, demand_form="saturating")
                base = dict(c.base, s_max=s)
                sc = _alt_scan(c, opts, base)
                row: dict[str, object] = {
                    "s_max": s,
                    "reversal_found": bool(sc.roots),
                    "roots": [r.value for r in sc.roots],
                }
                if be.variable_id in ("epsilon", "f"):
                    pred = (ana.predicted_epsilon_roots if be.variable_id == "epsilon" else ana.predicted_f_roots)(
                        base, opts
                    )
                    pred = [p for p in pred if be.search_low <= p <= be.search_high]
                    row["analytical_roots"] = pred
                    if len(pred) != len(sc.roots) or any(
                        not _root_close(a, b, c.rtol)
                        for a, b in zip(sorted(r.value for r in sc.roots), sorted(pred), strict=False)
                    ):
                        mismatch.append(f"s_max={s}: numeric {row['roots']} vs analytical {pred}")
                rows.append(row)
            lost = [r["s_max"] for r in rows if not r["reversal_found"]]
            desc = "saturating demand with s_max values (ILLUSTRATIVE placeholders) " + str(smax_list)
        else:
            sc = _alt_scan(c, dict(c.opts, demand_form="constant_elasticity"), c.base)
            rows.append(
                {
                    "demand_form": "constant_elasticity",
                    "reversal_found": bool(sc.roots),
                    "roots": [r.value for r in sc.roots],
                }
            )
            lost = [] if sc.roots else ["constant_elasticity"]
            desc = "constant-elasticity demand"
    except EcoBackfireError as exc:
        return mk(cid, fam, "inconclusive", f"alternative-form run raised: {exc}")
    if mismatch:
        return mk(
            cid,
            fam,
            "failed",
            "alternative-form numerics disagree with analytical prediction",
            {"rows": rows, "mismatch": mismatch},
        )
    if lost:
        return mk(
            cid,
            fam,
            "limits_claim",
            f"the reversal VANISHES under the alternative demand form for {lost}: "
            "the finding is conditional on the demand form",
            {"alternative": desc, "rows": rows, "vanishes_for": lost},
        )
    return mk(
        cid,
        fam,
        "passed",
        "the reversal persists under every alternative demand form tried",
        {"alternative": desc, "rows": rows},
    )


def check_boundary_variation(c: Ctx) -> CheckResult:
    cid, fam = "F4_boundary_variation", "boundary"
    early = _need_roots(c, cid, fam)
    if early:
        return early
    be = c.result.breakeven
    assert be is not None
    rows: dict[str, object] = {}
    first: dict[str, float | None] = {}
    try:
        for bv in ("B1", "B2", "B3"):
            sc = _alt_scan(c, dict(c.opts, boundary_variant=bv), c.base)
            first[bv] = _first_root_or_none(sc)
            rows[bv] = [r.value for r in sc.roots]
    except EcoBackfireError as exc:
        return mk(cid, fam, "inconclusive", f"boundary variant run raised: {exc}")
    problems: list[str] = []
    checked_predictions = False
    if be.variable_id == "epsilon" and c.opts["demand_form"] == "constant_elasticity":
        e1, e2, e3 = first["B1"], first["B2"], first["B3"]
        if e1 is not None and e2 is not None and e2 > e1 + 1e-9:
            problems.append(f"pre-registered prediction (d) violated: eps*_B2={e2} > eps*_B1={e1}")
        if e1 is not None and e3 is not None and e3 < e1 - 1e-9:
            problems.append(f"pre-registered prediction (e) violated: eps*_B3={e3} < eps*_B1={e1}")
        checked_predictions = True
    if problems:
        return mk(
            cid,
            fam,
            "failed",
            "boundary-variation outcome contradicts pre-registered predictions",
            {"roots_by_boundary": rows, "problems": problems},
        )
    gone = [bv for bv, r in first.items() if r is None]
    det = {
        "roots_by_boundary": rows,
        "predictions_d_e_checked": checked_predictions,
        "prediction_d": "eps*(B2) <= eps*(B1)",
        "prediction_e": "eps*(B3) >= eps*(B1)",
    }
    if gone:
        return mk(
            cid,
            fam,
            "limits_claim",
            f"the reversal is absent under boundary variant(s) {gone}: boundary-dependent",
            det,
        )
    return mk(
        cid,
        fam,
        "passed",
        "a break-even exists under all three boundary variants; shifts match pre-registered predictions"
        if checked_predictions
        else "a break-even exists under all three boundary variants",
        det,
    )


def check_parameter_corners(c: Ctx) -> CheckResult:
    cid, fam = "F5_parameter_uncertainty_corners", "parameter_uncertainty"
    early = _need_roots(c, cid, fam)
    if early:
        return early
    be = c.result.breakeven
    assert be is not None
    vars_ = [
        v
        for v in c.required
        if v != be.variable_id and isinstance(c.config.bindings[v].uncertainty, IntervalUncertainty)
    ]
    if not vars_:
        return mk(cid, fam, "inconclusive", "no input has an interval uncertainty to explore")
    if len(vars_) > 10:
        return mk(cid, fam, "inconclusive", "too many uncertain inputs for exhaustive corner enumeration")
    ends: dict[str, tuple[float, float]] = {}
    try:
        for v in vars_:
            u = c.config.bindings[v].uncertainty
            assert isinstance(u, IntervalUncertainty)
            ends[v] = (c.conv(v, u.low), c.conv(v, u.high))
        n = min(be.grid_points, 121)
        found: list[float] = []
        absent = 0
        total = 0
        for combo in itertools.product((0, 1), repeat=len(vars_)):
            vals = dict(c.base)
            for v, k in zip(vars_, combo, strict=True):
                vals[v] = ends[v][k]
            total += 1
            sc = c.scan(be.variable_id, be.search_low, be.search_high, n, 1e-10, base=vals)
            if sc.roots:
                found.append(sc.roots[0].value)
            else:
                absent += 1
    except PhysicalConstraintError as exc:
        return mk(cid, fam, "inconclusive", f"an interval endpoint violates a physical bound: {exc}")
    det = {
        "inputs_varied": vars_,
        "corners_evaluated": total,
        "corners_with_reversal": total - absent,
        "corners_without_reversal": absent,
        "first_break_even_min": min(found) if found else None,
        "first_break_even_max": max(found) if found else None,
        "interval_values_are": "ILLUSTRATIVE placeholders (not evidence)",
        "method_caveat": (
            "corner enumeration bounds the response only if it is monotone in each input; it is not a proof of bounds"
        ),
    }
    if absent:
        return mk(
            cid,
            fam,
            "limits_claim",
            f"no break-even exists within the search range at {absent} of {total} corners of the "
            "(illustrative) uncertainty box",
            det,
        )
    return mk(
        cid,
        fam,
        "passed",
        f"a reversal exists at all {total} corners; the threshold ranges over [{min(found):.6g}, {max(found):.6g}]",
        det,
    )


def check_extreme_inputs(c: Ctx) -> CheckResult:
    cid, fam = "F6_extreme_and_invalid_inputs", "edge"
    base, opts = c.base, c.opts
    saturating = opts["demand_form"] == "saturating"
    # (name, modifications, expectation) ; expectation: "ok" or exception class name
    cases: list[tuple[str, dict[str, float], str]] = [
        ("eta=0", {"eta": 0.0}, "ok"),
        ("eta->1", {"eta": 1.0 - 1e-12}, "ok"),
        ("eta=1 (invalid)", {"eta": 1.0}, "PhysicalConstraintError"),
        ("eta<0 (invalid)", {"eta": -0.1}, "PhysicalConstraintError"),
        ("phi tiny", {"phi": 1e-12}, "ok"),
        ("phi=0 (invalid)", {"phi": 0.0}, "PhysicalConstraintError"),
        ("phi>1 (invalid)", {"phi": 1.5}, "PhysicalConstraintError"),
        ("epsilon<0 (invalid)", {"epsilon": -1.0}, "PhysicalConstraintError"),
        ("epsilon=0", {"epsilon": 0.0}, "ok"),
        ("epsilon huge", {"epsilon": 1e6}, "ok" if saturating else "NumericalError"),
        ("L=0 (invalid)", {"L": 0.0}, "PhysicalConstraintError"),
        ("L tiny", {"L": 1e-9}, "ok"),
        ("E0=0 (invalid)", {"E0": 0.0}, "PhysicalConstraintError"),
        ("E0 tiny", {"E0": 1e-12}, "ok"),
        ("f=0", {"f": 0.0}, "ok"),
        ("f<0 (invalid)", {"f": -0.1}, "PhysicalConstraintError"),
        ("EM<0 (invalid)", {"EM1": -1.0}, "PhysicalConstraintError"),
        ("NaN input", {"E0": float("nan")}, "PhysicalConstraintError"),
        ("inf input", {"f": float("inf")}, "PhysicalConstraintError"),
    ]
    if saturating:
        cases.append(("s_max<1 (invalid)", {"s_max": 0.5}, "PhysicalConstraintError"))
    bad: list[str] = []
    for name, mod, expect in cases:
        vals = dict(base)
        vals.update(mod)
        try:
            out = c.model.evaluate(vals, opts)
            if expect != "ok":
                bad.append(f"{name}: expected {expect}, got a result")
            elif any(not math.isfinite(v) for v in out.values()):
                bad.append(f"{name}: non-finite output returned silently")
        except EcoBackfireError as exc:
            if type(exc).__name__ != expect:
                bad.append(f"{name}: expected {expect}, raised {type(exc).__name__}")
        except Exception as exc:  # any non-typed exception is itself a defect
            bad.append(f"{name}: untyped exception {type(exc).__name__}: {exc}")
    ok = not bad
    return mk(
        cid,
        fam,
        "passed" if ok else "failed",
        f"{len(cases)} extreme/invalid input cases behave as specified (finite result or typed error)"
        if ok
        else f"{len(bad)} extreme-input case(s) misbehaved",
        {"problems": bad, "cases": [n for n, _, _ in cases]},
    )


def check_numerical_stability(c: Ctx) -> CheckResult:
    cid, fam = "F8_numerical_stability", "stability"
    early = _need_roots(c, cid, fam)
    if early:
        return early
    be = c.result.breakeven
    assert be is not None and c.config.search is not None
    s = c.config.search
    problems: list[str] = []
    try:
        fine = c.scan(
            be.variable_id,
            be.search_low,
            be.search_high,
            (be.grid_points - 1) * c.config.verification.stability_grid_factor + 1,
            s.xtol / 10,
        )
        a = [r.value for r in be.roots]
        b = [r.value for r in fine.roots]
        if len(a) != len(b) or any(not _root_close(x, y, c.rtol) for x, y in zip(a, b, strict=True)):
            problems.append(f"refined grid/tolerance changed the roots: {a} -> {b}")
        ext_hi = be.search_high + 0.5 * (be.search_high - be.search_low)
        ext = c.scan(be.variable_id, be.search_low, ext_hi, be.grid_points * 2, s.xtol)
        inside = [r.value for r in ext.roots if r.value <= be.search_high]
        if len(inside) != len(a) or any(not _root_close(x, y, c.rtol) for x, y in zip(a, inside, strict=False)):
            problems.append("extending the search range changed the roots inside the original range")
        ext_note = "range extended by 50% above"
    except PhysicalConstraintError:
        ext_note = "range extension skipped: would violate a physical bound"
    except NumericalError as exc:
        ext_note = f"range extension hit a numerical limit: {exc}"
    ok = not problems
    return mk(
        cid,
        fam,
        "passed" if ok else "failed",
        "roots are stable under grid refinement, tighter tolerance, and range extension"
        if ok
        else "roots are NOT stable",
        {"problems": problems, "range_extension": ext_note},
        f"rtol={c.rtol:g}",
    )


def check_evidence_gate(c: Ctx) -> CheckResult:
    cid, fam = "F7_evidence_quality_gate", "evidence_gate"
    classes = {v: b.provenance_class for v, b in c.config.bindings.items() if not b.missing}
    ill = sorted(v for v, k in classes.items() if k is ProvenanceClass.ILLUSTRATIVE)
    unk = sorted(v for v, b in c.config.bindings.items() if b.uncertainty.kind == "unknown")
    det = {
        "weakest_provenance": c.result.weakest_provenance.value,
        "illustrative_inputs": ill,
        "inputs_with_unknown_uncertainty": unk,
    }
    if c.config.evidence_requests:
        det["evidence_resolved_inputs"] = sorted(c.config.evidence_requests)
        det["evidence_backed_inputs"] = (
            list(c.result.evidence_resolution.evidence_backed_inputs) if c.result.evidence_resolution else []
        )
    if ill or unk:
        return mk(
            cid,
            fam,
            "limits_claim",
            "inputs are illustrative and/or lack characterized uncertainty: the result demonstrates the method and "
            "cannot be a real-world conclusion",
            det,
        )
    return mk(cid, fam, "passed", "no illustrative inputs and all uncertainties characterized", det)


ALL_CHECKS: list[Callable[[Ctx], CheckResult]] = [
    check_closed_form,
    check_decimal_recompute,
    check_invariants,
    check_benchmarks,
    check_threshold_perturbation,
    check_counterexample_search,
    check_alternative_demand_form,
    check_boundary_variation,
    check_parameter_corners,
    check_extreme_inputs,
    check_numerical_stability,
    check_evidence_gate,
]

__all__ = ["ALL_CHECKS", "SUITE_VERSION", "Ctx"]
