"""Unit handling on top of pint (layer 0)."""

from __future__ import annotations

import math

import pint

from ecobackfire.errors import UnitError

_ureg = pint.UnitRegistry()
# CO2-equivalent is a *label* on a mass, not a separate dimension. Declared explicitly so the
# registry never guesses.
_ureg.define("kgCO2e = kilogram")
_ureg.define("gCO2e = gram")
_ureg.define("tCO2e = 1000 * kilogram")


def to_canonical(value: float, unit: str, canonical_unit: str, *, what: str = "value") -> float:
    """Convert ``value`` in ``unit`` to ``canonical_unit``; reject unknown or incompatible units."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise UnitError(f"{what}: value must be a finite number, got {value!r}")
    try:
        converted = _ureg.Quantity(float(value), unit).to(canonical_unit).magnitude
    except pint.UndefinedUnitError as exc:
        raise UnitError(f"{what}: unknown unit {unit!r}") from exc
    except pint.DimensionalityError as exc:
        raise UnitError(f"{what}: unit {unit!r} is incompatible with canonical unit {canonical_unit!r}") from exc
    except Exception as exc:  # malformed unit expression
        raise UnitError(f"{what}: cannot interpret unit {unit!r}: {exc}") from exc
    return float(converted)


def validate_unit_string(unit: str, *, what: str = "unit") -> None:
    """Raise UnitError unless ``unit`` parses."""
    try:
        _ureg.Unit(unit)
    except Exception as exc:
        raise UnitError(f"{what}: cannot interpret unit {unit!r}") from exc
