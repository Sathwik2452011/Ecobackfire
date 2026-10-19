"""Closed-form break-evens (docs/first-experiment.md section 3.3). Float arithmetic, stdlib only."""

from __future__ import annotations

import math
from collections.abc import Mapping


def embodied_effective(boundary: str, em0: float, em1: float) -> tuple[float, float]:
    if boundary == "B1":
        return em0, em1
    if boundary == "B2":
        return 0.0, em1
    if boundary == "B3":
        return 0.0, 0.0
    raise ValueError(f"unknown boundary variant {boundary!r}")


def embodied_burden_r(inputs: Mapping[str, float], boundary: str) -> float:
    em0e, em1e = embodied_effective(boundary, inputs["EM0"], inputs["EM1"])
    fe0 = inputs["f"] * inputs["E0"]
    if fe0 <= 0.0:
        raise ValueError("r is undefined when f*E0 == 0")
    return (em1e - em0e) / (inputs["L"] * fe0)


def eps_star(eta: float, phi: float, r: float) -> float | None:
    """Break-even elasticity for constant-elasticity demand; None if none exists for eps >= 0.

    eps* = [ln(1-r) - ln(1-eta)] / [-ln(1-phi*eta)]  (valid: 0<eta<1, 0<phi<=1, r<1);
    exists in the physical domain iff r <= eta.
    """
    if eta <= 0.0 or r >= 1.0:
        return None
    val = (math.log1p(-r) - math.log1p(-eta)) / (-math.log1p(-phi * eta))
    return val if val >= 0.0 else None


def predicted_epsilon_roots(inputs: Mapping[str, float], options: Mapping[str, str]) -> list[float]:
    eta, phi = inputs["eta"], inputs["phi"]
    r = embodied_burden_r(inputs, options["boundary_variant"])
    root = eps_star(eta, phi, r)
    if root is None:
        return []
    if options["demand_form"] == "saturating" and not (1.0 - eta) * inputs["s_max"] > 1.0 - r:
        return []  # service cap binds before the decision can reverse
    return [root]


def service_ratio(inputs: Mapping[str, float], options: Mapping[str, str]) -> float:
    s = (1.0 - inputs["phi"] * inputs["eta"]) ** (-inputs["epsilon"])
    if options["demand_form"] == "saturating":
        s = min(s, inputs["s_max"])
    return s


def predicted_f_roots(inputs: Mapping[str, float], options: Mapping[str, str]) -> list[float]:
    """Break-even emissions factor f*: dC(f) = -f*E0*[1-(1-eta)S] + dEM/L = 0."""
    s = service_ratio(inputs, options)
    em0e, em1e = embodied_effective(options["boundary_variant"], inputs["EM0"], inputs["EM1"])
    denom = inputs["E0"] * (1.0 - (1.0 - inputs["eta"]) * s)
    num = (em1e - em0e) / inputs["L"]
    if denom == 0.0:
        return []
    f_star = num / denom
    return [f_star] if f_star >= 0.0 else []
