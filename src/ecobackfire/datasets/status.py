"""Evidence status vocabulary (pure: no pydantic, no pint).

``EvidenceStatus`` is a *view* over the existing ``ProvenanceClass`` plus review state. It adds no new way to
store a value: a record's status is always derived, never free text.

Record-level statuses (a stored record can have exactly one)::

    measured            ProvenanceClass.MEASURED  (review state is reported separately)
    verified_source     ProvenanceClass.SOURCED and a named human reviewed the citation
    sourced_unreviewed  ProvenanceClass.SOURCED and nobody has reviewed the citation  (not in the brief's
                        minimum list; added so an unchecked citation is never shown as "verified")
    derived             ProvenanceClass.ESTIMATED or SIMULATED (computed from other things; see the record's
                        own provenance class for which)
    assumption          ProvenanceClass.USER_SUPPLIED
    illustrative        ProvenanceClass.ILLUSTRATIVE

Resolution-level statuses (an *outcome* of looking for a value; never stored on a record)::

    missing             no usable record exists for the request
    conflicting         two or more usable records disagree beyond their documented uncertainty

``weakest_status`` orders record statuses by origin only. It is not a confidence score, and it is NOT the gate
for the phrase "evidence-backed" (that gate is ``ecobackfire.evidence.is_evidence_backed``).
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class EvidenceStatus(StrEnum):
    VERIFIED_SOURCE = "verified_source"
    SOURCED_UNREVIEWED = "sourced_unreviewed"
    MEASURED = "measured"
    DERIVED = "derived"
    ASSUMPTION = "assumption"
    ILLUSTRATIVE = "illustrative"
    MISSING = "missing"
    CONFLICTING = "conflicting"


RECORD_STATUSES: tuple[EvidenceStatus, ...] = (
    EvidenceStatus.MEASURED,
    EvidenceStatus.VERIFIED_SOURCE,
    EvidenceStatus.SOURCED_UNREVIEWED,
    EvidenceStatus.DERIVED,
    EvidenceStatus.ASSUMPTION,
    EvidenceStatus.ILLUSTRATIVE,
)
RESOLUTION_ONLY_STATUSES: tuple[EvidenceStatus, ...] = (EvidenceStatus.MISSING, EvidenceStatus.CONFLICTING)

# Coarse origin ordering, strongest first (same rank = same origin class). Policy table, not a measurement.
_RANK: dict[EvidenceStatus, int] = {
    EvidenceStatus.MEASURED: 0,
    EvidenceStatus.VERIFIED_SOURCE: 0,
    EvidenceStatus.SOURCED_UNREVIEWED: 1,
    EvidenceStatus.DERIVED: 2,
    EvidenceStatus.ASSUMPTION: 3,
    EvidenceStatus.ILLUSTRATIVE: 4,
}

# Keys are the values of ecobackfire.contracts.common.ProvenanceClass. A test asserts every member is covered.
_PROVENANCE_TO_STATUS: dict[str, EvidenceStatus] = {
    "measured": EvidenceStatus.MEASURED,
    "estimated": EvidenceStatus.DERIVED,
    "simulated": EvidenceStatus.DERIVED,
    "user_supplied": EvidenceStatus.ASSUMPTION,
    "illustrative": EvidenceStatus.ILLUSTRATIVE,
}

STATUS_MEANING: dict[EvidenceStatus, str] = {
    EvidenceStatus.MEASURED: "direct measurement with a documented method (source cited)",
    EvidenceStatus.VERIFIED_SOURCE: "value from a cited source; a named human reviewed the citation",
    EvidenceStatus.SOURCED_UNREVIEWED: "value from a cited source; the citation has NOT been reviewed",
    EvidenceStatus.DERIVED: "estimated or simulated from other inputs; see the record's derivation",
    EvidenceStatus.ASSUMPTION: "supplied by a person as an assumption; not evidence",
    EvidenceStatus.ILLUSTRATIVE: "placeholder that exercises the model; not evidence",
    EvidenceStatus.MISSING: "no usable record exists for the request",
    EvidenceStatus.CONFLICTING: "usable records disagree beyond their documented uncertainty",
}


def status_from_provenance(provenance_class: str, *, reviewed: bool) -> EvidenceStatus:
    """Map a ProvenanceClass value (str or StrEnum) and review state to a record-level status."""
    key = str(getattr(provenance_class, "value", provenance_class))
    if key == "sourced":
        return EvidenceStatus.VERIFIED_SOURCE if reviewed else EvidenceStatus.SOURCED_UNREVIEWED
    try:
        return _PROVENANCE_TO_STATUS[key]
    except KeyError:
        raise ValueError(f"unknown provenance class {key!r}; no evidence status is defined for it") from None


def is_resolution_only(status: EvidenceStatus) -> bool:
    return status in RESOLUTION_ONLY_STATUSES


def status_rank(status: EvidenceStatus) -> int:
    """0 = strongest origin. Resolution-only statuses have no rank (ValueError)."""
    try:
        return _RANK[status]
    except KeyError:
        raise ValueError(f"{status.value!r} is a resolution outcome and has no origin rank") from None


def weakest_status(statuses: Iterable[EvidenceStatus]) -> EvidenceStatus | None:
    """Weakest record-level status among ``statuses`` (ties: first encountered). Empty input -> None."""
    items = list(statuses)
    if not items:
        return None
    return max(items, key=status_rank)
