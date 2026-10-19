"""SYNTHETIC dataset fixtures for the dataset-layer tests.

Everything here is made up to exercise validation and registry logic. The source id
'fixture:not-a-real-source' is not a real source, the values are not environmental data, and none of this may
be copied into a real dataset file.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from ecobackfire.contracts.common import ILLUSTRATIVE_MARKER

SYN_SOURCE = {"source_id": "fixture:not-a-real-source", "citation": "Synthetic test citation (not real)"}
SYN_REVIEW = {"reviewer": "test reviewer", "reviewed_on": "2026-01-02", "statement": "synthetic review"}


def evidence(
    record_id: str = "r1", variable_id: str = "f", value: float = 0.4, unit: str = "kgCO2e/kWh", **kw: Any
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "record_id": record_id,
        "variable_id": variable_id,
        "description": "synthetic value",
        "value": value,
        "unit": unit,
        "provenance_class": "sourced",
        "source": dict(SYN_SOURCE),
        "scope": {"geography": "synthetic region", "period": "2000"},
    }
    base.update(kw)
    return base


def illustrative_evidence(
    record_id: str = "ill", variable_id: str = "epsilon", value: float = 0.5, unit: str = "dimensionless"
) -> dict[str, Any]:
    return evidence(
        record_id, variable_id, value, unit, provenance_class="illustrative", source=None, note=ILLUSTRATIVE_MARKER
    )


def record(ev: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"evidence": ev or evidence()}
    d.update(kw)
    return d


def release(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "dataset_id": "synthetic-fixture",
        "release_version": "1.0.0",
        "title": "Synthetic fixture dataset (not real data)",
        "description": "Exercises the dataset layer.",
        "provider": "synthetic test provider",
        "last_updated": "2026-01-01",
        "changelog": "initial synthetic release",
        "records": [record()],
    }
    base.update(over)
    return copy.deepcopy(base)


def write_release(root: Path, rel: dict[str, Any], *, indent: int | None = 2) -> Path:
    d = root / rel["dataset_id"] / rel["release_version"]
    d.mkdir(parents=True, exist_ok=True)
    p = d / "dataset.json"
    p.write_text(json.dumps(rel, indent=indent), encoding="utf-8")
    return p
