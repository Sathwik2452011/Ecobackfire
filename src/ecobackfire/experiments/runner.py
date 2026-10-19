"""Experiment runner: compare, breakeven, oat_sweep. Pure function of (config, model)."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ecobackfire.contracts.common import (
    IntervalUncertainty,
    QuantityValue,
    weakest_provenance,
)
from ecobackfire.contracts.experiment import (
    BreakevenResult,
    BreakevenRoot,
    Decision,
    ExperimentConfiguration,
    ExperimentResult,
    OATRow,
    ProvenanceSummaryRow,
    Scenario,
    ScenarioResult,
    SensitivityResult,
)
from ecobackfire.contracts.model import DecisionMetric
from ecobackfire.core.engine import PreparedInputs, applicability_warnings, prepare_inputs
from ecobackfire.core.hashing import canonical_json, sha256_text
from ecobackfire.core.model import Model
from ecobackfire.core.trace import Trace
from ecobackfire.datasets.registry import DatasetRegistry
from ecobackfire.errors import ConfigError, PhysicalConstraintError
from ecobackfire.experiments.breakeven import BreakevenScan, find_breakevens
from ecobackfire.experiments.evidence import materialize_config
from ecobackfire.models.registry import get_model
from ecobackfire.units import to_canonical


def config_sha256(config: ExperimentConfiguration) -> str:
    return sha256_text(canonical_json(config.model_dump(mode="json")))


def get_metric(model: Model, metric_id: str) -> DecisionMetric:
    for m in model.spec.decision_metrics:
        if m.id == metric_id:
            return m
    raise ConfigError(
        f"decision metric {metric_id!r} not defined by {model.spec.ref}; "
        f"available: {[m.id for m in model.spec.decision_metrics]}"
    )


def _output_unit(model: Model, output_id: str) -> str:
    for o in model.spec.outputs:
        if o.id == output_id:
            return o.canonical_unit
    return ""


def classify_decision(metric: DecisionMetric, out: Mapping[str, float]) -> Decision:
    value = out[metric.output_id]
    scale = max(abs(out.get("C0", 0.0)), abs(out.get("C1", 0.0)), 1.0)
    tol = 1e-12 * scale
    d = value - metric.threshold
    if abs(d) <= tol:
        label = "indeterminate"
    elif (d < 0) == (metric.comparator == "lt"):
        label = "beneficial"
    else:
        label = "not_beneficial"
    return Decision(metric_id=metric.id, value=value, unit="", label=label)  # type: ignore[arg-type]


def _convert(config: ExperimentConfiguration, model: Model, vid: str, value: float) -> float:
    """Convert a number expressed in binding ``vid``'s unit to the model's canonical unit."""
    b = config.bindings.get(vid)
    if b is None:
        raise ConfigError(f"{vid!r} is not bound, so a value in its unit cannot be interpreted")
    return to_canonical(value, b.unit, model.spec.input_by_id(vid).canonical_unit, what=vid)


def make_g(
    model: Model, opts: Mapping[str, str], metric: DecisionMetric, base: Mapping[str, float], var: str
) -> Callable[[float], float]:
    def g(x: float) -> float:
        values = dict(base)
        values[var] = x
        out = model.evaluate(values, opts)
        d = out[metric.output_id] - metric.threshold
        return d if metric.comparator == "lt" else -d

    return g


def search_bounds(config: ExperimentConfiguration, model: Model) -> tuple[float, float]:
    assert config.search is not None
    return (
        _convert(config, model, config.search.variable_id, config.search.low),
        _convert(config, model, config.search.variable_id, config.search.high),
    )


def run_breakeven_scan(
    model: Model,
    opts: Mapping[str, str],
    metric: DecisionMetric,
    base: Mapping[str, float],
    var: str,
    lo: float,
    hi: float,
    n: int,
    xtol: float,
) -> BreakevenScan:
    return find_breakevens(make_g(model, opts, metric, base, var), lo, hi, n, xtol)


def _qv(model: Model, out: Mapping[str, float]) -> dict[str, QuantityValue]:
    return {k: QuantityValue(value=v, unit=_output_unit(model, k)) for k, v in sorted(out.items())}


def _compare(
    config: ExperimentConfiguration,
    model: Model,
    opts: dict[str, str],
    metric: DecisionMetric,
    base: dict[str, float],
) -> list[ScenarioResult]:
    scenarios = config.scenarios or [Scenario(name="base_inputs", overrides={})]
    results: list[ScenarioResult] = []
    for sc in scenarios:
        values = dict(base)
        for vid, val in sc.overrides.items():
            values[vid] = _convert(config, model, vid, val)
        tr = Trace()
        out = model.evaluate(values, opts, trace=tr)
        dec = classify_decision(metric, out)
        dec = dec.model_copy(update={"unit": _output_unit(model, metric.output_id)})
        results.append(
            ScenarioResult(
                name=sc.name, overrides=dict(sc.overrides), outputs=_qv(model, out), decision=dec, trace=tr.steps
            )
        )
    return results


def _breakeven(
    config: ExperimentConfiguration,
    model: Model,
    opts: dict[str, str],
    metric: DecisionMetric,
    base: dict[str, float],
    warnings: list[str],
) -> BreakevenResult:
    assert config.search is not None
    s = config.search
    lo, hi = search_bounds(config, model)
    scan = run_breakeven_scan(model, opts, metric, base, s.variable_id, lo, hi, s.grid_points, s.xtol)
    warnings.extend(scan.warnings)
    for label, x in (("low", lo), ("high", hi)):
        vals = dict(base)
        vals[s.variable_id] = x
        for w in applicability_warnings(model.spec, {s.variable_id: x}):
            warnings.append(f"search range ({label} end) {w}")
    trace_steps: list[dict[str, object]] = []
    if scan.roots:
        vals = dict(base)
        vals[s.variable_id] = scan.roots[0].value
        tr = Trace()
        model.evaluate(vals, opts, trace=tr)
        trace_steps = tr.steps
    return BreakevenResult(
        variable_id=s.variable_id,
        unit=model.spec.input_by_id(s.variable_id).canonical_unit,
        search_low=lo,
        search_high=hi,
        grid_points=s.grid_points,
        xtol=s.xtol,
        classification=scan.classification,
        roots=[BreakevenRoot(**r.__dict__) for r in scan.roots],
        scan=scan.scan,
        note=scan.note,
        trace_at_first_root=trace_steps,
    )


def _oat(
    config: ExperimentConfiguration,
    model: Model,
    opts: dict[str, str],
    metric: DecisionMetric,
    base: dict[str, float],
    required: list[str],
) -> SensitivityResult:
    assert config.oat is not None
    spec = config.oat
    search = config.search

    def target(values: dict[str, float]) -> float | None:
        if spec.target == "delta_emissions":
            return model.evaluate(values, opts)[metric.output_id]
        assert search is not None
        lo, hi = search_bounds(config, model)
        sc = run_breakeven_scan(
            model, opts, metric, values, search.variable_id, lo, hi, search.grid_points, search.xtol
        )
        return sc.roots[0].value if sc.roots else None

    base_target = target(dict(base))
    target_unit = (
        _output_unit(model, metric.output_id)
        if spec.target == "delta_emissions"
        else model.spec.input_by_id(search.variable_id).canonical_unit
        if search
        else ""
    )
    rows: list[OATRow] = []
    skipped: list[str] = []
    for vid in required:
        if spec.target == "breakeven" and search and vid == search.variable_id:
            skipped.append(f"{vid}: is the searched variable")
            continue
        b = config.bindings[vid]
        assert b.value is not None
        if vid in spec.ranges:
            lo_u, hi_u = spec.ranges[vid]
            source = "config_range"
        elif isinstance(b.uncertainty, IntervalUncertainty):
            lo_u, hi_u = b.uncertainty.low, b.uncertainty.high
            source = "binding_interval"
        else:
            if b.value == 0.0:
                skipped.append(f"{vid}: zero base value; relative perturbation undefined")
                continue
            d = abs(b.value) * spec.relative_perturbation
            lo_u, hi_u = b.value - d, b.value + d
            source = "relative_perturbation"
        try:
            lo_c = _convert(config, model, vid, lo_u)
            hi_c = _convert(config, model, vid, hi_u)
            vlo = dict(base)
            vlo[vid] = lo_c
            vhi = dict(base)
            vhi[vid] = hi_c
            t_lo, t_hi = target(vlo), target(vhi)
        except PhysicalConstraintError as exc:
            skipped.append(f"{vid}: perturbed value violates a physical bound ({exc})")
            continue
        swing = abs(t_hi - t_lo) if t_lo is not None and t_hi is not None else None
        rows.append(
            OATRow(
                variable_id=vid,
                unit=b.unit,
                base_value=b.value,
                low_value=lo_u,
                high_value=hi_u,
                range_source=source,
                target_at_low=t_lo,
                target_at_high=t_hi,
                swing=swing,
            )  # type: ignore[arg-type]
        )
    rows.sort(key=lambda r: (r.swing is None, -(r.swing or 0.0), r.variable_id))
    return SensitivityResult(
        target=spec.target,
        target_unit=target_unit,
        method="one-at-a-time (OAT)",
        base_target=base_target,
        rows=rows,
        skipped=skipped,
    )


def run_experiment(
    config: ExperimentConfiguration, model: Model | None = None, *, registry: DatasetRegistry | None = None
) -> ExperimentResult:
    """Run ``config``. If it carries ``evidence_requests`` they are resolved against ``registry`` first (an
    unresolved or conflicting request raises; nothing is substituted) and the EFFECTIVE configuration - requests
    plus the resolved bindings - is what is hashed and run. Without requests nothing changes."""
    model = model or get_model(config.model_ref)
    if model.spec.ref != config.model_ref:
        raise ConfigError(f"model {model.spec.ref} does not match config model_ref {config.model_ref}")
    config, evidence = materialize_config(config, registry, model.spec)
    opts = model.resolve_options(config.options)
    metric = get_metric(model, config.decision_metric_ref)
    required = model.required_inputs(opts)
    prepared: PreparedInputs = prepare_inputs(
        model.spec, config.bindings, required, verified_evidence=evidence.verified_evidence
    )
    base = prepared.values
    warnings = list(prepared.warnings)
    unused = sorted(set(config.bindings) - set(required))
    if unused:
        warnings.append(f"bound but unused under the chosen options: {unused}")
    if config.search is not None and config.search.variable_id not in required:
        raise ConfigError(f"search variable {config.search.variable_id!r} is not a required model input")
    if evidence.summary is not None and evidence.summary.inputs_without_evidence:
        warnings.append(
            "inputs without evidence (assumed or illustrative; see the evidence resolution table): "
            f"{evidence.summary.inputs_without_evidence}"
        )

    compare = breakeven = sensitivity = None
    if config.kind == "compare":
        compare = _compare(config, model, opts, metric, base)
    elif config.kind == "breakeven":
        breakeven = _breakeven(config, model, opts, metric, base, warnings)
    else:
        sensitivity = _oat(config, model, opts, metric, base, required)
        if config.oat and config.oat.target == "breakeven":
            # also report the base breakeven so the table is interpretable
            breakeven = _breakeven(config, model, opts, metric, base, warnings)

    prov_rows = [
        ProvenanceSummaryRow(variable_id=vid, provenance_class=b.provenance_class, uncertainty_kind=b.uncertainty.kind)
        for vid, b in sorted(config.bindings.items())
        if not b.missing
    ]
    weakest = weakest_provenance([r.provenance_class for r in prov_rows])
    assert weakest is not None
    return ExperimentResult(
        experiment_id=config.experiment_id,
        config_sha256=config_sha256(config),
        model_ref=model.spec.ref,
        kind=config.kind,
        status="completed_with_warnings" if warnings else "completed",
        options=opts,
        system_boundary=model.system_boundary(opts),
        functional_unit=model.spec.assumptions.functional_unit,
        base_inputs={
            k: QuantityValue(value=v, unit=model.spec.input_by_id(k).canonical_unit) for k, v in sorted(base.items())
        },
        compare=compare,
        breakeven=breakeven,
        sensitivity=sensitivity,
        input_provenance_summary=prov_rows,
        weakest_provenance=weakest,
        warnings=warnings,
        evidence_resolution=evidence.summary,
    )
