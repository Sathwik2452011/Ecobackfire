"""SYNTHETIC registries for the evidence-resolution tests.

Everything here is made up to exercise resolver logic. Dataset id 'synthetic-resolution', source id
'fixture:not-a-real-source' and every value are NOT environmental data and must never be copied into a real
dataset. The REAL release (owid-electricity-carbon-intensity) is only used in tests/scientific.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _dataset_fixtures import evidence, release, write_release

from ecobackfire.datasets.registry import DatasetRegistry

DS = "synthetic-resolution"
VER = "1.0.0"


def f_record(
    record_id: str = "f.in.2024",
    value: float = 705.4,
    unit: str = "gCO2e/kWh",
    geo: str | None = "IN",
    start: str | None = "2024",
    end: str | None = "2024",
    uncertainty: dict[str, Any] | None = None,
    quality: str | None = None,
    variable_id: str = "f",
    **ev_kw: Any,
) -> dict[str, Any]:
    """A SYNTHETIC dataset record for variable ``f`` with structured geography and period."""
    ev = evidence(
        record_id,
        variable_id,
        value,
        unit,
        scope={"geography": geo or "unspecified", "period": f"{start}" if start else "unspecified"},
        **ev_kw,
    )
    if uncertainty is not None:
        ev["uncertainty"] = uncertainty
    rec: dict[str, Any] = {"evidence": ev}
    if geo is not None:
        rec["geography_codes"] = [geo] if isinstance(geo, str) else list(geo)
    if start is not None and end is not None:
        rec["temporal"] = {"start": start, "end": end}
    if quality is not None:
        rec["quality"] = quality
    return rec


def build_registry(root: Path, records: list[dict[str, Any]], **release_over: Any) -> DatasetRegistry:
    write_release(root, release(dataset_id=DS, release_version=VER, records=records, **release_over))
    return DatasetRegistry.from_directory(root)


def request(**over: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"dataset_id": DS, "version": VER, "geography": "IN", "period": "2024"}
    d.update(over)
    return d
