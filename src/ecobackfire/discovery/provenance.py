"""Join keys between a discovery candidate's evidence chain and the later analyses (pure, no pydantic calls).

Falsification (``falsification.py``) and robustness (``robustness.py``) are pure numerics over a decision function
``g(x)``; they know nothing about datasets. A candidate carries ``input_evidence`` (one ``InputEvidenceRef`` per
model input). These helpers turn that into the identifiers those modules already accept, so a robustness
perturbation of an evidence-backed input names the exact record it perturbs.

Wiring falsification/robustness INTO ``run_discovery`` is a later step; this module only makes the chain addressable.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ecobackfire.contracts.resolution import InputEvidenceRef
from ecobackfire.core.hashing import canonical_json, sha256_text


def evidence_ids_for(refs: Sequence[InputEvidenceRef], names: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """input name -> the evidence id that supplies it (empty tuple when the input is assumed/illustrative).

    Unknown names raise ``KeyError`` rather than silently returning 'no evidence'."""
    by = {r.variable_id: r for r in refs}
    return {n: ((by[n].evidence_id,) if by[n].evidence_id else ()) for n in names}


def provenance_digest(refs: Sequence[InputEvidenceRef]) -> str:
    """Stable SHA-256 over a candidate's input evidence (order independent): equal digests = same inputs from the
    same records, values and releases."""
    payload = sorted((r.model_dump(mode="json") for r in refs), key=lambda d: str(d["variable_id"]))
    return sha256_text(canonical_json(payload))
