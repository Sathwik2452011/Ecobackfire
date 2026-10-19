"""EvidenceRecord: one quantity, its unit, scope, uncertainty, provenance, and (optional) human review.

Integrity rules (enforced at construction):
* ``measured`` / ``sourced`` records must name a real-looking source (id plus a citation or URL).
* ``illustrative`` records must carry the standard marker and must NOT cite a source.
* Only ``measured`` / ``sourced`` records can carry a human review.
* Nothing in this module can fetch or verify a source. A citation is a claim; "reviewed" means a
  named human states they compared the cited source with the value.

``classify_tier`` is the single place that decides how a record may be described downstream.
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from enum import StrEnum

from pydantic import Field, model_validator

from ecobackfire.contracts.common import (
    ILLUSTRATIVE_MARKER,
    Frozen,
    IntervalUncertainty,
    ProvenanceClass,
    Uncertainty,
    UnknownUncertainty,
)

EVIDENCE_SCHEMA_VERSION = "1.0.0"

_URL_RE = re.compile(r"^(https?://[^\s/]+\S*|doi:10\.\d{4,9}/\S+)$", re.IGNORECASE)
_PLACEHOLDER_EXACT = frozenset({"n/a", "na", "none", "unknown", "example", "sample", "xxx", "?", "-"})
_PLACEHOLDER_TOKENS = frozenset({"tbd", "todo", "placeholder", "fake", "dummy", "lorem"})


def _iso_date(value: str, what: str) -> None:
    try:
        _dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{what} must be an ISO date (YYYY-MM-DD), got {value!r}") from None


def _looks_like_placeholder(text: str) -> bool:
    t = text.strip().lower()
    if t in _PLACEHOLDER_EXACT:
        return True
    return any(tok in _PLACEHOLDER_TOKENS for tok in re.split(r"[^a-z0-9]+", t))


def is_valid_url(text: str) -> bool:
    """Public wrapper: http(s) URL or ``doi:10.xxxx/...`` (same rule SourceRef applies)."""
    return bool(_URL_RE.match(text))


def looks_like_placeholder(text: str) -> bool:
    """Public wrapper over the placeholder heuristic (a heuristic, not proof a source is real)."""
    return _looks_like_placeholder(text)


class SourceRef(Frozen):
    """Pointer to where a value comes from. Never auto-generated; supplied by a human or a real dataset."""

    source_id: str  # DOI, dataset identifier, report number, ...
    citation: str | None = None
    url: str | None = None
    locator: str | None = None  # table / page / section / dataset column
    retrieved_on: str | None = None  # ISO date

    @model_validator(mode="after")
    def _check(self) -> SourceRef:
        if not self.source_id.strip():
            raise ValueError("source_id must be non-empty")
        if _looks_like_placeholder(self.source_id):
            raise ValueError(f"source_id {self.source_id!r} looks like a placeholder, not a source")
        if self.citation is not None and (not self.citation.strip() or _looks_like_placeholder(self.citation)):
            raise ValueError("citation must be real text, not empty or a placeholder")
        if self.url is not None and not _URL_RE.match(self.url):
            raise ValueError(f"url {self.url!r} must be http(s)://... or doi:10.xxxx/...")
        if self.retrieved_on is not None:
            _iso_date(self.retrieved_on, "retrieved_on")
        return self


class Scope(Frozen):
    """Where and when the value applies. Free text on purpose; absence is reported, never defaulted."""

    geography: str | None = None
    period: str | None = None
    system_description: str | None = None  # e.g. device class / technology the value describes


class ReviewRecord(Frozen):
    """A named human states they compared the cited source with the recorded value."""

    reviewer: str
    reviewed_on: str
    statement: str

    @model_validator(mode="after")
    def _check(self) -> ReviewRecord:
        if not self.reviewer.strip() or not self.statement.strip():
            raise ValueError("review requires a reviewer and a statement")
        _iso_date(self.reviewed_on, "reviewed_on")
        return self


class EvidenceTier(StrEnum):
    ILLUSTRATIVE = "illustrative"  # placeholder for demonstration; not evidence
    ASSUMPTION = "assumption"  # user-supplied or estimated; labelled assumption, not sourced fact
    SYNTHETIC = "synthetic"  # simulated / derived numerical result
    CITED_UNREVIEWED = "cited_unreviewed"  # cites a source; nobody has checked the citation
    CITED_REVIEWED = "cited_reviewed"  # a named human reviewed the citation against the value


class EvidenceRecord(Frozen):
    schema_version: str = EVIDENCE_SCHEMA_VERSION
    record_id: str
    variable_id: str  # the model input this record may support
    description: str
    value: float
    unit: str
    uncertainty: Uncertainty = Field(default_factory=UnknownUncertainty)
    provenance_class: ProvenanceClass
    source: SourceRef | None = None
    scope: Scope = Field(default_factory=Scope)
    review: ReviewRecord | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _integrity(self) -> EvidenceRecord:
        if not self.record_id or any(c.isspace() for c in self.record_id):
            raise ValueError("record_id must be non-empty and contain no whitespace")
        if not self.variable_id.strip() or not self.unit.strip() or not self.description.strip():
            raise ValueError("variable_id, unit and description must be non-empty")
        if not math.isfinite(self.value):
            raise ValueError("value must be finite")
        cls = self.provenance_class
        cited = cls in (ProvenanceClass.MEASURED, ProvenanceClass.SOURCED)
        if cited:
            if self.source is None:
                raise ValueError(f"{cls.value} records require a source")
            if not (self.source.citation or self.source.url):
                raise ValueError(f"{cls.value} records require a citation or a url in the source")
        else:
            if self.review is not None:
                raise ValueError("only measured/sourced records can carry a review")
            if cls is ProvenanceClass.ILLUSTRATIVE and self.source is not None:
                raise ValueError("an illustrative record must not cite a source")
        if cls is ProvenanceClass.ILLUSTRATIVE and (not self.note or ILLUSTRATIVE_MARKER not in self.note):
            raise ValueError(f"illustrative records must carry the note {ILLUSTRATIVE_MARKER!r}")
        u = self.uncertainty
        if isinstance(u, IntervalUncertainty) and not (u.low <= self.value <= u.high):
            raise ValueError("value must lie inside its own uncertainty interval")
        return self


def classify_tier(record: EvidenceRecord) -> EvidenceTier:
    cls = record.provenance_class
    if cls is ProvenanceClass.ILLUSTRATIVE:
        return EvidenceTier.ILLUSTRATIVE
    if cls in (ProvenanceClass.USER_SUPPLIED, ProvenanceClass.ESTIMATED):
        return EvidenceTier.ASSUMPTION
    if cls is ProvenanceClass.SIMULATED:
        return EvidenceTier.SYNTHETIC
    return EvidenceTier.CITED_REVIEWED if record.review is not None else EvidenceTier.CITED_UNREVIEWED


def completeness(record: EvidenceRecord) -> tuple[float, list[str]]:
    """Fraction of seven documentation criteria met, plus the list of what is missing.

    This measures documentation, not truth, and never changes the tier.
    """
    src = record.source
    checks = {
        "source": src is not None,
        "citation_or_url": src is not None and bool(src.citation or src.url),
        "locator": src is not None and bool(src.locator),
        "geography": bool(record.scope.geography),
        "period": bool(record.scope.period),
        "uncertainty_characterized": not isinstance(record.uncertainty, UnknownUncertainty),
        "human_review": record.review is not None,
    }
    missing = [k for k, ok in checks.items() if not ok]
    return (len(checks) - len(missing)) / len(checks), missing


def evidence_backed_issues(record: EvidenceRecord) -> list[str]:
    """Reasons this record may NOT be described as evidence-backed. Empty list = eligible."""
    tier = classify_tier(record)
    issues: list[str] = []
    if tier is not EvidenceTier.CITED_REVIEWED:
        issues.append(f"tier is '{tier.value}', not 'cited_reviewed'")
    if not record.scope.geography:
        issues.append("geographic scope not stated")
    if not record.scope.period:
        issues.append("temporal scope not stated")
    if isinstance(record.uncertainty, UnknownUncertainty):
        issues.append("uncertainty not characterized")
    return issues


def is_evidence_backed(record: EvidenceRecord) -> bool:
    return not evidence_backed_issues(record)

