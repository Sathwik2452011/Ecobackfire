"""SYNTHETIC fixtures for evidence-sensitive evaluation. TEST-ONLY: every value, source id and dataset id here is
invented to exercise the machinery. Nothing is evidence about any real system, and none of it may be imported
into a registry or a report as data. No pydantic-free shortcuts: records are the real contract types.
"""

from __future__ import annotations

from ecobackfire.contracts.common import IntervalUncertainty, PointUncertainty, ProvenanceClass
from ecobackfire.contracts.resolution import (
    ConversionRecord,
    EvidenceRequest,
    InputResolution,
    ResolvedRecord,
    SourceSummary,
)

SYNTHETIC_DATASET = "synthetic-fixture"  # test-only id: not a real dataset
SYNTHETIC_VERSION = "0.0.1"
UNIT = "1"


def record(
    rid: str,
    value: float,
    *,
    low: float | None = None,
    high: float | None = None,
    source: str | None = None,
    geography: str = "SYNTHETIC-GEO",
    period: str = "2024",
) -> ResolvedRecord:
    """A synthetic resolved record. ``low``/``high`` together make it document an interval; otherwise it has none."""
    unc = IntervalUncertainty(low=low, high=high) if low is not None and high is not None else PointUncertainty()
    rng = (low, high) if low is not None and high is not None else None
    return ResolvedRecord(
        evidence_id=f"{SYNTHETIC_DATASET}@{SYNTHETIC_VERSION}:{rid}",
        dataset_id=SYNTHETIC_DATASET,
        version=SYNTHETIC_VERSION,
        record_id=rid,
        status="sourced_unreviewed",
        provenance_class=ProvenanceClass.SOURCED,
        quality="synthetic",
        reviewed=False,
        evidence_backed=False,
        evidence_backed_issues=["synthetic test fixture"],
        value=value,
        unit=UNIT,
        conversion=ConversionRecord(
            original_value=value,
            original_unit=UNIT,
            canonical_value=value,
            canonical_unit=UNIT,
            factor=1.0,
            performed=False,
        ),
        uncertainty=unc,
        range_canonical=rng,
        source=SourceSummary(
            source_id=source or f"synthetic-source-{rid}", citation="synthetic fixture, not a real source"
        ),
        geography_text=geography,
        geography_codes=[geography],
        period_text=period,
        temporal_start=period,
        temporal_end=period,
        geography_match="exact",
        temporal_match="contains",
        methodology=None,
        note=None,
        release_file_sha256="0" * 64,
        release_content_sha256="1" * 64,
    )


def resolution(
    variable: str, records: list[ResolvedRecord], *, select: str | None = None, state: str | None = None
) -> InputResolution:
    """Build an InputResolution the way the resolver reports it: resolved (one record or a caller selection),
    conflicting (several, nobody chose), or missing (none)."""
    req = EvidenceRequest(dataset_id=SYNTHETIC_DATASET, version=SYNTHETIC_VERSION, geography="GLOBAL", period="2024")
    if not records:
        return InputResolution(variable_id=variable, basis="evidence", state="missing", request=req)
    chosen = next((r for r in records if r.evidence_id == select), None) if select else None
    if chosen is None and len(records) == 1:
        chosen = records[0]
    if chosen is not None:
        return InputResolution(
            variable_id=variable, basis="evidence", state="resolved", request=req, selected=chosen,
            selection_rule="caller_selected" if select else "single_applicable_record", applicable=list(records),
        )
    from ecobackfire.contracts.resolution import ConflictReport

    vals = {r.evidence_id: r.conversion.canonical_value for r in records}
    conflict = ConflictReport(
        evidence_ids=sorted(vals), canonical_values=vals,
        max_relative_spread=(max(vals.values()) - min(vals.values())) / max(abs(v) for v in vals.values()),
        rule="synthetic",
    )
    return InputResolution(variable_id=variable, basis="evidence", state="conflicting", request=req,
                           applicable=list(records), conflict=conflict)
