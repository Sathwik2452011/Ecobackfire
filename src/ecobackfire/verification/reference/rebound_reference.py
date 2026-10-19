"""Independent 60-digit Decimal recomputation of the rebound model and its break-evens.

Written WITHOUT importing the production model or runner. Different arithmetic (exact decimal,
exp/ln instead of float pow), different root finder (Decimal bisection), different grid code.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, localcontext

PREC = 60


def _eval(
    inputs: Mapping[str, float], options: Mapping[str, str], var: str | None, x: Decimal | None
) -> dict[str, Decimal]:
    """Evaluate with input ``var`` overridden by the Decimal ``x`` (no float round-trip)."""

    def get(k: str) -> Decimal:
        if var is not None and k == var and x is not None:
            return x
        return Decimal(inputs[k])  # exact binary expansion of the float

    E0, eta, phi, eps = get("E0"), get("eta"), get("phi"), get("epsilon")
    f, L = get("f"), get("L")
    pi = Decimal(1) - phi * eta
    s = (-eps * pi.ln()).exp()
    if options["demand_form"] == "saturating":
        s = min(s, get("s_max"))
    E1 = (Decimal(1) - eta) * s * E0
    b = options["boundary_variant"]
    em0 = get("EM0") if b == "B1" else Decimal(0)
    em1 = get("EM1") if b in ("B1", "B2") else Decimal(0)
    C0 = f * E0 + em0 / L
    C1 = f * E1 + em1 / L
    return {"E1": E1, "service_ratio": s, "C0": C0, "C1": C1, "dC": C1 - C0}


def evaluate(inputs: Mapping[str, float], options: Mapping[str, str]) -> dict[str, Decimal]:
    with localcontext() as ctx:
        ctx.prec = PREC
        return _eval(inputs, options, None, None)


def breakevens(
    inputs: Mapping[str, float], options: Mapping[str, str], var: str, lo: float, hi: float, n: int
) -> list[Decimal]:
    """All sign changes of dC(var) on an n-point grid, each refined by 200 Decimal bisections."""
    with localcontext() as ctx:
        ctx.prec = PREC

        def g(x: Decimal) -> Decimal:
            return _eval(inputs, options, var, x)["dC"]

        a, b = Decimal(lo), Decimal(hi)
        pts = [a + (b - a) * i / (n - 1) for i in range(n)]
        gs = [g(p) for p in pts]
        roots: list[Decimal] = [pts[i] for i in range(n) if gs[i] == 0]
        for i in range(n - 1):
            if gs[i] != 0 and gs[i + 1] != 0 and ((gs[i] < 0) != (gs[i + 1] < 0)):
                x0, x1, g0 = pts[i], pts[i + 1], gs[i]
                for _ in range(200):
                    mid = (x0 + x1) / 2
                    gm = g(mid)
                    if gm == 0:
                        x0 = x1 = mid
                        break
                    if (gm < 0) == (g0 < 0):
                        x0, g0 = mid, gm
                    else:
                        x1 = mid
                roots.append((x0 + x1) / 2)
        return sorted(roots)
