"""Canonical JSON and SHA-256 helpers (deterministic artifacts)."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


def canonical_json(obj: Any) -> str:
    """Compact, key-sorted JSON. NaN/Infinity are rejected (never persisted)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def pretty_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonicalize(obj: Any) -> Any:
    """Deep copy with every mapping key-sorted, tuples as lists, and non-finite floats rejected.

    The canonical form is what ``pretty_json``/``canonical_json`` serialize, so
    ``canonicalize(json.loads(pretty_json(x))) == canonicalize(x)`` and any text derived from a canonicalized
    object is the same before and after a JSON round trip.
    """
    if isinstance(obj, dict):
        return {str(k): canonicalize(obj[k]) for k in sorted(obj, key=str)}
    if isinstance(obj, (list, tuple)):
        return [canonicalize(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        raise ValueError(f"non-finite number {obj!r} cannot be canonicalized")
    return obj
