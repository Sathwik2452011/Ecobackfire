"""Evidence-resolution contracts (layer 0): what a caller asks for, and what the resolver reports.

The resolver itself lives in ``ecobackfire.datasets.resolution`` (layer 2). These models are layer 0 so that
``ExperimentConfiguration``, ``ExperimentResult``, ``DiscoverySpec`` and ``DiscoveryResult`` can carry the
evidence chain without importing the dataset layer.

Vocabulary
* ``state``  - outcome of looking for evidence for ONE model input: ``resolved`` (exactly one record supplies the
  value, possibly after an explicit, recorded selection), ``conflicting`` (applicable records disagree and nobody
  chose), ``missing`` (no appropriate record). Never stored on a record.
* ``basis``  - where a bound input comes from in a run: ``evidence`` (resolved from a dataset record),
  ``assumed`` (user_supplied / estimated / simulated binding) or ``illustrative``.

Nothing here validates against a registry; that is the resolver's job.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ecobackfire.contracts.common import Frozen, ProvenanceClass, Uncertainty

RESOLUTION_SCHEMA_VERSION = "1.0.0"

ResolutionState = Literal["resolved", "conflicting", "missing"]
InputBasis = Literal["evidence", "assumed", "illustrative"]
RecordStatusName = Literal["measured", "verified_source", "sourced_unreviewed", "derived", "assumption", "illustrative"]
GeographyMatch = Literal["exact", "broader", "global"]
TemporalMatch = Literal["contains", "overlaps"]

_PERIOD_PART = r"[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?"
_PERIOD_RE = re.compile(rf"^{_PERIOD_PART}(?:/{_PERIOD_PART})?$")


class EvidenceRequest(Frozen):
    """Ask the dataset registry for ONE model input.

    Everything that could silently change the answer is explicit: dataset id, exact version (there is no
    'latest'), geography, period. Fallback and looser matching are opt-ins, never defaults.
    """

    dataset_id: str
    version: str
    #: Requested geography code: ``GLOBAL``, ``M49:NNN``, ISO alpha-2 (``IN``) or ISO 3166-2 (``IN-AP``).
    geography: str
    #: ``YYYY``, ``YYYY-MM``, ``YYYY-MM-DD`` or ``START/END`` of those (a partial date spans its whole period).
    period: str
    #: Allow a record scoped to a broader geography (country for a subdivision, GLOBAL for anything). Default no.
    geography_fallback: bool = False
    #: ``contains``: the record's period must contain the requested period. ``overlaps``: any overlap suffices.
    temporal_match: TemporalMatch = "contains"
    #: Reject records whose origin is weaker than this (``status_rank`` ordering); None = no status filter.
    minimum_status: RecordStatusName | None = None
    #: Allow records flagged ``deprecated`` (``withdrawn`` records are never resolved).
    include_deprecated: bool = False
    #: Caller's explicit choice among applicable records: a qualified id ``<dataset>@<version>:<record>``.
    select: str | None = None
    note: str | None = None

    @field_validator("dataset_id", "version", "geography", "period")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip() or v != v.strip():
            raise ValueError("must be a non-empty string without surrounding whitespace")
        return v

    @field_validator("version")
    @classmethod
    def _exact(cls, v: str) -> str:
        if v.lower() in ("latest", "*", "any"):
            raise ValueError("an exact dataset version is required (no 'latest')")
        return v

    @field_validator("period")
    @classmethod
    def _period_syntax(cls, v: str) -> str:
        if not _PERIOD_RE.match(v):
            raise ValueError(f"period {v!r} must be YYYY[-MM[-DD]] or START/END of those")
        return v

    @model_validator(mode="after")
    def _select_matches_request(self) -> EvidenceRequest:
        if self.select is not None:
            prefix = f"{self.dataset_id}@{self.version}:"
            if not self.select.startswith(prefix) or len(self.select) == len(prefix):
                raise ValueError(f"select must be a qualified id starting with {prefix!r}, got {self.select!r}")
        return self


class ConversionRecord(Frozen):
    """A unit conversion performed during resolution. The original value/unit are never overwritten."""

    original_value: float
    original_unit: str
    canonical_value: float
    canonical_unit: str
    #: canonical_value / original_value (None when the original is 0). A scale factor, for readers only.
    factor: float | None
    performed: bool  # False when the units are identical (nothing was converted)
    method: str = "pint (ecobackfire.units.to_canonical)"


class SourceSummary(Frozen):
    source_id: str
    citation: str | None = None
    url: str | None = None
    locator: str | None = None
    retrieved_on: str | None = None


class ResolvedRecord(Frozen):
    """One applicable record, with its complete evidence chain."""

    evidence_id: str  # <dataset>@<version>:<record_id>
    dataset_id: str
    version: str
    record_id: str
    status: RecordStatusName
    provenance_class: ProvenanceClass
    quality: str
    reviewed: bool
    evidence_backed: bool
    evidence_backed_issues: list[str]
    value: float  # in ``unit`` exactly as the record states it
    unit: str
    conversion: ConversionRecord
    uncertainty: Uncertainty
    #: Canonical-unit interval when the record states one. None = no range is available (never invented).
    range_canonical: tuple[float, float] | None
    source: SourceSummary | None
    geography_text: str | None
    geography_codes: list[str]
    period_text: str | None
    temporal_start: str | None
    temporal_end: str | None
    geography_match: GeographyMatch
    temporal_match: TemporalMatch
    methodology: str | None
    note: str | None
    release_file_sha256: str
    release_content_sha256: str
    record_metadata: dict[str, str] = Field(default_factory=dict)


class ExcludedReason(Frozen):
    code: str
    detail: str


class ExcludedRecord(Frozen):
    """A record (or a whole dataset/version) that was looked at and NOT used, with every reason."""

    evidence_id: str
    reasons: list[ExcludedReason]


class ConflictReport(Frozen):
    evidence_ids: list[str]
    canonical_values: dict[str, float]
    max_relative_spread: float
    rule: str
    #: True when the caller pinned one record with ``select`` although the records disagree.
    overridden_by_caller: bool = False


class InputResolution(Frozen):
    """The resolution of one model input (evidence-requested, or reported as assumed/illustrative)."""

    variable_id: str
    basis: InputBasis
    provenance_class: ProvenanceClass | None = None
    #: Present for basis='evidence' (and for the stand-alone resolver, where it can be missing/conflicting).
    state: ResolutionState | None = None
    request: EvidenceRequest | None = None
    selected: ResolvedRecord | None = None
    selection_rule: str | None = None
    applicable: list[ResolvedRecord] = Field(default_factory=list)
    excluded: list[ExcludedRecord] = Field(default_factory=list)
    conflict: ConflictReport | None = None
    warnings: list[str] = Field(default_factory=list)
    decision_log: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> InputResolution:
        if self.state == "resolved" and self.selected is None:
            raise ValueError("a resolved input must name its selected record")
        if self.state in ("conflicting", "missing") and self.selected is not None:
            raise ValueError(f"a '{self.state}' input must not carry a selected record")
        if self.state == "conflicting" and (self.conflict is None or len(self.applicable) < 2):
            raise ValueError("a conflicting input must carry the conflict report and all applicable records")
        if self.basis != "evidence" and (self.selected is not None or self.state is not None):
            raise ValueError("assumed/illustrative inputs carry no evidence resolution")
        return self

    def ref(self, role: str) -> InputEvidenceRef:
        s = self.selected
        return InputEvidenceRef(
            variable_id=self.variable_id,
            role=role,  # type: ignore[arg-type]
            basis=self.basis,
            provenance_class=self.provenance_class,
            evidence_id=s.evidence_id if s else None,
            status=s.status if s else None,
            source_id=s.source.source_id if s and s.source else None,
            geography=s.geography_text if s else None,
            period=s.period_text if s else None,
            value=s.value if s else None,
            unit=s.unit if s else None,
            canonical_value=s.conversion.canonical_value if s else None,
            conversion_performed=s.conversion.performed if s else None,
            evidence_backed=s.evidence_backed if s else None,
            selection_rule=self.selection_rule,
            geography_match=s.geography_match if s else None,
            release_content_sha256=s.release_content_sha256 if s else None,
        )


class InputEvidenceRef(Frozen):
    """Compact per-input provenance carried on every discovery candidate (full chain: evidence_resolution)."""

    variable_id: str
    role: Literal["searched", "held_at_base"]
    basis: InputBasis
    provenance_class: ProvenanceClass | None
    evidence_id: str | None
    status: RecordStatusName | None
    source_id: str | None
    geography: str | None
    period: str | None
    value: float | None
    unit: str | None
    canonical_value: float | None
    conversion_performed: bool | None
    evidence_backed: bool | None
    selection_rule: str | None
    geography_match: GeographyMatch | None
    release_content_sha256: str | None


class ReleaseRef(Frozen):
    dataset_id: str
    version: str
    file_sha256: str
    content_sha256: str


class EvidenceResolutionSummary(Frozen):
    """Everything about where a run's inputs came from. Present only when evidence was requested."""

    schema_version: str = RESOLUTION_SCHEMA_VERSION
    registry_sha256: str
    releases_used: list[ReleaseRef]
    inputs: list[InputResolution]  # every bound input, sorted by variable id
    n_evidence: int
    n_assumed: int
    n_illustrative: int
    evidence_backed_inputs: list[str]  # inputs that pass ``is_evidence_backed`` (reviewed, scoped, uncertain)
    inputs_without_evidence: list[str]  # assumed + illustrative
    note: str

    def by_variable(self) -> dict[str, InputResolution]:
        return {r.variable_id: r for r in self.inputs}


def omit_empty(data: dict[str, object], *keys: str) -> dict[str, object]:
    """Drop ``keys`` from a serialized model when they are empty/None, so configurations and results that do not
    use evidence serialize (and hash) exactly as they did before evidence resolution existed."""
    for k in keys:
        if k in data and not data[k]:
            del data[k]
    return data
