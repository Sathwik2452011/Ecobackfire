"""Efficiency-improvement / direct-rebound / embodied-emissions model (version 1.0.0).

Implements equations EQ1-EQ8 of docs/first-experiment.md section 3. Pure and deterministic:
no I/O, no randomness, no clock. Inputs are floats in the spec's canonical units.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from ecobackfire.contracts.common import FunctionalUnit, SystemBoundary
from ecobackfire.contracts.model import (
    Assumption,
    DecisionMetric,
    EnvironmentalModel,
    EnvironmentalVariable,
    Equation,
    ModelAssumptions,
    PhysicalBounds,
)
from ecobackfire.core.engine import check_physical_bounds
from ecobackfire.core.trace import Trace
from ecobackfire.errors import ConfigError, MissingEvidenceError, NumericalError

MODEL_ID = "efficiency_rebound"
MODEL_VERSION = "1.0.0"

OPTION_DEFAULTS = {"boundary_variant": "B1", "demand_form": "constant_elasticity"}
BOUNDARY_VARIANTS = {
    "B1": "operational + annualized embodied emissions; decision at end of life of the old device",
    "B2": "early replacement: old device's embodied emissions are sunk (EM0 treated as 0)",
    "B3": "operational emissions only (embodied emissions excluded: EM0 = EM1 = 0)",
}


def _v(
    vid: str,
    name: str,
    desc: str,
    dim: str,
    unit: str,
    kind: str = "parameter",
    bounds: PhysicalBounds | None = None,
    qk: str | None = None,
) -> EnvironmentalVariable:
    return EnvironmentalVariable(
        id=vid,
        name=name,
        description=desc,
        dimension=dim,
        canonical_unit=unit,
        kind=kind,  # type: ignore[arg-type]
        physical_bounds=bounds,
        quantity_kind=qk,
    )


SPEC = EnvironmentalModel(
    model_id=MODEL_ID,
    version=MODEL_VERSION,
    title="Efficiency improvement with direct rebound and embodied emissions",
    description=(
        "Compares a baseline technology with a more energy-efficient intervention delivering the "
        "same service, accounting for a demand response (constant-elasticity by default) and the "
        "annualized embodied emissions of the devices."
    ),
    domain="energy.efficiency",
    inputs=[
        _v(
            "E0",
            "baseline annual energy use",
            "Final energy per user per year, baseline device",
            "energy/time",
            "kWh/yr",
            bounds=PhysicalBounds(min=0.0, inclusive_min=False),
        ),
        _v(
            "eta",
            "fractional energy-intensity reduction",
            "Fractional reduction of energy per unit service: e1 = (1-eta)*e0",
            "dimensionless",
            "dimensionless",
            bounds=PhysicalBounds(min=0.0, max=1.0, inclusive_max=False),
            qk="fraction",
        ),
        _v(
            "phi",
            "energy share of marginal service cost",
            "Share of the per-unit-service marginal cost that scales with energy use",
            "dimensionless",
            "dimensionless",
            bounds=PhysicalBounds(min=0.0, max=1.0, inclusive_min=False),
            qk="fraction",
        ),
        _v(
            "epsilon",
            "own-price elasticity of service demand (absolute value)",
            "Constant-elasticity demand: S1/S0 = (p1/p0)^(-epsilon). Short/long-run and direct-only "
            "definitions must be stated by whoever supplies the value.",
            "dimensionless",
            "dimensionless",
            bounds=PhysicalBounds(min=0.0),
            qk="elasticity",
        ),
        _v(
            "f",
            "emissions factor of delivered energy",
            "Average emissions per kWh of final energy (marginal vs average must be stated by the supplier)",
            "mass/energy",
            "kgCO2e/kWh",
            bounds=PhysicalBounds(min=0.0),
        ),
        _v(
            "EM0",
            "embodied emissions, baseline device",
            "Cradle-to-gate, per device",
            "mass",
            "kgCO2e",
            bounds=PhysicalBounds(min=0.0),
        ),
        _v(
            "EM1",
            "embodied emissions, intervention device",
            "Cradle-to-gate, per device",
            "mass",
            "kgCO2e",
            bounds=PhysicalBounds(min=0.0),
        ),
        _v(
            "L",
            "amortization period (device lifetime)",
            "Embodied emissions are spread linearly over L",
            "time",
            "yr",
            bounds=PhysicalBounds(min=0.0, inclusive_min=False),
        ),
        _v(
            "s_max",
            "service saturation multiplier",
            "Alternative demand form only: S1/S0 is capped at s_max",
            "dimensionless",
            "dimensionless",
            bounds=PhysicalBounds(min=1.0),
        ),
    ],
    outputs=[
        _v("E1", "intervention annual energy", "", "energy/time", "kWh/yr", kind="output"),
        _v("service_ratio", "S1/S0", "", "dimensionless", "dimensionless", kind="output"),
        _v("C0", "baseline annual emissions (boundary-dependent)", "", "mass/time", "kgCO2e/yr", kind="output"),
        _v("C1", "intervention annual emissions (boundary-dependent)", "", "mass/time", "kgCO2e/yr", kind="output"),
        _v("dC", "change in annual emissions (C1 - C0)", "", "mass/time", "kgCO2e/yr", kind="metric"),
        _v("dC_normalized", "dC / (f*E0)", "", "dimensionless", "dimensionless", kind="output"),
        _v(
            "r_embodied_burden", "embodied burden r = dEM/(L f E0)", "", "dimensionless", "dimensionless", kind="output"
        ),
        _v("rebound_R", "rebound R = 1 - (E0-E1)/(eta E0)", "", "dimensionless", "dimensionless", kind="output"),
        _v(
            "intensity_ratio",
            "(C1/S1)/(C0/S0): emissions per unit of service, intervention / baseline",
            "",
            "dimensionless",
            "dimensionless",
            kind="output",
        ),
    ],
    options={
        "boundary_variant": sorted(BOUNDARY_VARIANTS),
        "demand_form": ["constant_elasticity", "saturating"],
    },
    assumptions=ModelAssumptions(
        assumptions=[
            Assumption(
                id="A1",
                statement="Constant-elasticity demand applied to the effective price per unit of service (EQ1-EQ2).",
                rationale="Standard first-order rebound formulation; analytically tractable.",
                testable=True,
                test_ref="F3_alternative_demand_form",
            ),
            Assumption(
                id="A2",
                statement="Only direct rebound is modelled.",
                rationale="Keeps the first slice analyzable; indirect rebound is a declared exclusion.",
            ),
            Assumption(
                id="A3",
                statement="Elasticity is symmetric and constant over the price change considered.",
                rationale="Simplification.",
                testable=True,
                test_ref="F3_alternative_demand_form",
            ),
            Assumption(
                id="A4",
                statement="Energy price and emissions factor are constant over the amortization period.",
                rationale="Static annual model; time-varying grid is a planned variant.",
                testable=False,
            ),
            Assumption(
                id="A5",
                statement="Baseline and intervention deliver the same quality of service per unit.",
                rationale="Defines the functional unit.",
            ),
            Assumption(
                id="A6",
                statement=(
                    "Embodied emissions are amortized linearly over L with no maintenance or replacement emissions."
                ),
                rationale="Simplification.",
            ),
            Assumption(
                id="A7",
                statement=(
                    "One emissions factor applies to all energy (no marginal/average distinction, no dispatch effects)."
                ),
                rationale="Simplification.",
            ),
        ],
        exclusions=[
            "indirect and economy-wide rebound (re-spending of savings)",
            "grid response to changed load; marginal vs average emissions",
            "end-of-life processing; maintenance and replacement emissions",
            "impact categories other than GHG (CO2e)",
            "welfare effects of increased service",
        ],
        functional_unit=FunctionalUnit(
            id="FU1",
            description="Annual delivered service of one representative user of the service class over one year",
            quantity_unit="service units/yr (abstract; cancels in the primary metric)",
            reference_flow_description=(
                "Primary metric: absolute annual emissions (kgCO2e/yr per user). Secondary metric: "
                "emissions per unit of service (intensity_ratio)."
            ),
        ),
        system_boundary=SystemBoundary(
            id="B1",
            description=BOUNDARY_VARIANTS["B1"],
            included_stages=["operational energy use", "embodied emissions (amortized)"],
            excluded_stages=[
                "indirect rebound",
                "end-of-life processing",
                "grid dynamics",
                "maintenance and replacement",
            ],
            cutoff_rules="none declared",
        ),
        applicability={
            "E0": (1.0, 1.0e5),
            "eta": (0.0, 0.8),
            "phi": (0.05, 1.0),
            "epsilon": (0.0, 3.0),
            "f": (0.0, 1.5),
            "EM0": (0.0, 1.0e5),
            "EM1": (0.0, 1.0e5),
            "L": (1.0, 40.0),
            "s_max": (1.0, 5.0),
        },
        known_limitations=[
            "Validity ranges above are modelling-judgement placeholders, not evidence-based.",
            "Results demonstrate the method; they are not recommendations about real products.",
            "Constant-elasticity demand may not describe any real service.",
        ],
    ),
    decision_metrics=[
        DecisionMetric(
            id="delta_emissions_beneficial",
            output_id="dC",
            comparator="lt",
            threshold=0.0,
            direction_text="intervention is beneficial iff absolute annual emissions fall (dC < 0)",
        )
    ],
    equations=[
        Equation(id="EQ1", text="pi = 1 - phi*eta"),
        Equation(id="EQ2", text="S1/S0 = pi^(-epsilon)"),
        Equation(id="EQ2b", text="S1/S0 = min(pi^(-epsilon), s_max)   [demand_form = saturating]"),
        Equation(id="EQ3", text="E1 = (1-eta) * (S1/S0) * E0"),
        Equation(id="EQ4", text="C0 = f*E0 + EM0e/L ;  C1 = f*E1 + EM1e/L   (EM0e, EM1e per boundary variant)"),
        Equation(id="EQ5", text="dC = C1 - C0"),
        Equation(id="EQ6", text="r = (EM1e-EM0e)/(L*f*E0) ;  dC/(f*E0) = (1-eta)*(S1/S0) - 1 + r"),
        Equation(id="EQ7", text="R = 1 - (E0-E1)/(eta*E0)   [eta > 0]"),
        Equation(id="EQ8", text="intensity_ratio = (C1/S1)/(C0/S0) = (C1/C0)/(S1/S0)"),
    ],
    status="draft",
)

_BASE_INPUTS = ["E0", "eta", "phi", "epsilon", "f", "EM0", "EM1", "L"]


def resolve_options(options: Mapping[str, str]) -> dict[str, str]:
    """Fill documented defaults and reject unknown option names or values."""
    resolved = dict(OPTION_DEFAULTS)
    for k, v in options.items():
        if k not in OPTION_DEFAULTS:
            raise ConfigError(f"unknown model option {k!r}; allowed: {sorted(OPTION_DEFAULTS)}")
        if v not in SPEC.options[k]:
            raise ConfigError(f"option {k}={v!r} not allowed; allowed: {SPEC.options[k]}")
        resolved[k] = v
    return resolved


class EfficiencyReboundModel:
    spec = SPEC

    def resolve_options(self, options: Mapping[str, str]) -> dict[str, str]:
        return resolve_options(options)

    def system_boundary(self, options: Mapping[str, str]) -> SystemBoundary:
        """The system boundary actually in force for the chosen ``boundary_variant``."""
        variant = resolve_options(options)["boundary_variant"]
        base = self.spec.assumptions.system_boundary
        common_ex = ["indirect rebound", "end-of-life processing", "grid dynamics", "maintenance and replacement"]
        if variant == "B1":
            return base
        if variant == "B2":
            return SystemBoundary(
                id="B2",
                description=BOUNDARY_VARIANTS["B2"],
                included_stages=["operational energy use", "embodied emissions of the new device"],
                excluded_stages=["embodied emissions of the old device (sunk)", *common_ex],
                cutoff_rules="none declared",
            )
        return SystemBoundary(
            id="B3",
            description=BOUNDARY_VARIANTS["B3"],
            included_stages=["operational energy use"],
            excluded_stages=["embodied emissions (both devices)", *common_ex],
            cutoff_rules="none declared",
        )

    def required_inputs(self, options: Mapping[str, str]) -> list[str]:
        opts = resolve_options(options)
        req = list(_BASE_INPUTS)
        if opts["demand_form"] == "saturating":
            req.append("s_max")
        return req

    def evaluate(
        self,
        inputs: Mapping[str, float],
        options: Mapping[str, str],
        trace: Trace | None = None,
    ) -> dict[str, float]:
        opts = resolve_options(options)
        for vid in self.required_inputs(opts):
            if vid not in inputs:
                raise MissingEvidenceError(f"required input {vid!r} missing at evaluation")
        check_physical_bounds(self.spec, inputs)

        E0, eta, phi = inputs["E0"], inputs["eta"], inputs["phi"]
        eps, f, L = inputs["epsilon"], inputs["f"], inputs["L"]

        pi = 1.0 - phi * eta
        if trace:
            trace.step("EQ1", "pi = 1 - phi*eta", {"phi": phi, "eta": eta}, {"pi": pi})
        try:
            s_ratio = math.pow(pi, -eps)
        except OverflowError as exc:
            raise NumericalError(
                f"service response overflows (pi={pi}, epsilon={eps}); input is numerically "
                "unrepresentable, not a valid result"
            ) from exc
        if not math.isfinite(s_ratio):
            raise NumericalError("non-finite service response")
        if trace:
            trace.step("EQ2", "S1/S0 = pi^(-epsilon)", {"pi": pi, "epsilon": eps}, {"service_ratio": s_ratio})
        if opts["demand_form"] == "saturating":
            capped = min(s_ratio, inputs["s_max"])
            if trace:
                trace.step(
                    "EQ2b",
                    "S1/S0 = min(pi^(-epsilon), s_max)",
                    {"uncapped": s_ratio, "s_max": inputs["s_max"]},
                    {"service_ratio": capped},
                )
            s_ratio = capped

        E1 = (1.0 - eta) * s_ratio * E0
        if trace:
            trace.step(
                "EQ3", "E1 = (1-eta)*(S1/S0)*E0", {"eta": eta, "service_ratio": s_ratio, "E0": E0}, {"E1": E1}, "kWh/yr"
            )

        em0, em1 = inputs["EM0"], inputs["EM1"]
        if opts["boundary_variant"] == "B2":
            em0e, em1e = 0.0, em1
        elif opts["boundary_variant"] == "B3":
            em0e, em1e = 0.0, 0.0
        else:
            em0e, em1e = em0, em1
        C0 = f * E0 + em0e / L
        C1 = f * E1 + em1e / L
        if trace:
            trace.step(
                "EQ4",
                "C0 = f*E0 + EM0e/L ; C1 = f*E1 + EM1e/L",
                {"f": f, "E0": E0, "E1": E1, "EM0e": em0e, "EM1e": em1e, "L": L},
                {"C0": C0, "C1": C1},
                "kgCO2e/yr",
            )
        dC = C1 - C0
        if trace:
            trace.step("EQ5", "dC = C1 - C0", {"C0": C0, "C1": C1}, {"dC": dC}, "kgCO2e/yr")

        out: dict[str, float] = {
            "E1": E1,
            "service_ratio": s_ratio,
            "C0": C0,
            "C1": C1,
            "dC": dC,
        }
        fE0 = f * E0
        if fE0 > 0.0:
            r = (em1e - em0e) / (L * fE0)
            out["r_embodied_burden"] = r
            out["dC_normalized"] = (1.0 - eta) * s_ratio - 1.0 + r
            if trace:
                trace.step(
                    "EQ6",
                    "r = dEM/(L f E0); dC/(f E0) = (1-eta)*(S1/S0) - 1 + r",
                    {"dEM": em1e - em0e, "L": L, "f": f, "E0": E0},
                    {"r": r, "dC_normalized": out["dC_normalized"]},
                )
        if eta > 0.0:
            out["rebound_R"] = 1.0 - (E0 - E1) / (eta * E0)
            if trace:
                trace.step(
                    "EQ7", "R = 1 - (E0-E1)/(eta*E0)", {"E0": E0, "E1": E1, "eta": eta}, {"rebound_R": out["rebound_R"]}
                )
        if C0 > 0.0:
            out["intensity_ratio"] = (C1 / C0) / s_ratio
            if trace:
                trace.step(
                    "EQ8",
                    "intensity_ratio = (C1/C0)/(S1/S0)",
                    {"C0": C0, "C1": C1, "service_ratio": s_ratio},
                    {"intensity_ratio": out["intensity_ratio"]},
                )
        for k, val in out.items():
            if not math.isfinite(val):
                raise NumericalError(f"non-finite output {k}")
        return out
