"""Versioned dataset registry (layer 2), built on the evidence layer.

Lazy exports (PEP 562): importing a pure submodule such as ``datasets.versioning`` does NOT import pydantic or
pint; only touching ``DatasetRegistry`` / ``DatasetRelease`` / ... does.
"""

from __future__ import annotations

import importlib
from typing import Any

_EXPORTS = {
    "EvidenceStatus": "status",
    "status_from_provenance": "status",
    "weakest_status": "status",
    "Version": "versioning",
    "parse_version": "versioning",
    "latest_version": "versioning",
    "Period": "temporal",
    "DatasetRelease": "schema",
    "DatasetRecord": "schema",
    "QualityFlag": "schema",
    "LicenseInfo": "schema",
    "TemporalCoverage": "schema",
    "RawFile": "schema",
    "MissingVariable": "schema",
    "DatasetRegistry": "registry",
    "RegistryEntry": "registry",
    "ParameterCandidate": "registry",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    return getattr(importlib.import_module(f"{__name__}.{module}"), name)
