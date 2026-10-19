"""Temporal scope helpers (pure). Accepts ``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD``.

A partial date denotes its whole span: as a start, ``2022`` means 2022-01-01; as an end, 2022-12-31.
Plausibility of the years (e.g. 1800-2200) is a validation-stage question, not enforced here.
"""

from __future__ import annotations

import calendar
import datetime as _dt
import re
from collections.abc import Iterable
from dataclasses import dataclass

_RE = re.compile(r"^([0-9]{4})(?:-([0-9]{2})(?:-([0-9]{2}))?)?$")


@dataclass(frozen=True)
class Period:
    start: _dt.date
    end: _dt.date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"period end {self.end} is before start {self.start}")

    def label(self) -> str:
        return f"{self.start.isoformat()}/{self.end.isoformat()}"


_FULL = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def parse_iso_date(text: str, what: str = "date") -> _dt.date:
    """Strict full ISO date YYYY-MM-DD."""
    if isinstance(text, str) and _FULL.match(text):
        try:
            return _dt.date.fromisoformat(text)
        except ValueError:
            pass
    raise ValueError(f"{what} must be an ISO date (YYYY-MM-DD), got {text!r}")


def parse_period_bound(text: str, *, end: bool, what: str = "period bound") -> _dt.date:
    m = _RE.match(text) if isinstance(text, str) else None
    if m is None:
        raise ValueError(f"{what} must be YYYY, YYYY-MM or YYYY-MM-DD, got {text!r}")
    year, month, day = int(m.group(1)), m.group(2), m.group(3)
    try:
        if month is None:
            return _dt.date(year, 12, 31) if end else _dt.date(year, 1, 1)
        mo = int(month)
        if day is None:
            last = calendar.monthrange(year, mo)[1]  # raises ValueError for month outside 1..12
            return _dt.date(year, mo, last if end else 1)
        return _dt.date(year, mo, int(day))
    except ValueError:
        raise ValueError(f"{what} {text!r} is not a valid calendar date") from None


def make_period(start: str, end: str) -> Period:
    return Period(parse_period_bound(start, end=False, what="start"), parse_period_bound(end, end=True, what="end"))


def overlaps(a: Period, b: Period) -> bool:
    return a.start <= b.end and b.start <= a.end


def contains(outer: Period, inner: Period) -> bool:
    return outer.start <= inner.start and inner.end <= outer.end


def hull(periods: Iterable[Period]) -> Period | None:
    items = list(periods)
    if not items:
        return None
    return Period(min(p.start for p in items), max(p.end for p in items))
