"""Deterministic text formatting helpers for reports (pure; stdlib only, so testable without the model stack).

Why this exists (regression: ``verify`` then ``reproduce`` reported ``report.md: content hash mismatch``)
-------------------------------------------------------------------------------------------------------
Artifacts are written with key-sorted JSON (``core.hashing.pretty_json``). A report rendered from the IN-MEMORY
result iterated dicts in insertion order; a report rendered after a JSON round trip (``verify`` reloads
``result.json``) iterated them in sorted order. Same science, different bytes. The rule here is that report
text may depend on a mapping's KEYS and VALUES only, never on its insertion order: mappings are always rendered
in sorted-key order.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping


def format_kv(mapping: Mapping[str, object], fmt: Callable[[object], str]) -> str:
    """``k=v, k=v`` with keys in sorted order, whatever the mapping's insertion order."""
    return ", ".join(f"{k}={fmt(mapping[k])}" for k in sorted(mapping))
