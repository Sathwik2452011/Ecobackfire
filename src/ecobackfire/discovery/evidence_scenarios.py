"""Turn the evidence that exists for one model input into explicit evaluation scenarios (layer 4, pure).

Input: an ``InputResolution`` (as produced by ``datasets.resolution.resolve_parameter`` / carried in an
``EvidenceResolutionSummary``). Output: an ``EvidenceScenarioSet``.

Rules (documented; none is a statistical assumption)
* Every APPLICABLE record is evaluated. A resolution that selected one record (caller choice or single
  applicable record) still exposes the other applicable records as alternatives; a conflicting resolution
  exposes all of them and has no baseline. No record is dropped because of what it would show.
* A record that states an interval uncertainty yields three scenarios: ``lower``, ``central`` (the record's own
  value) and ``upper``. A record without an interval yields one ``source`` scenario. Nothing is added where the
  source documents nothing: no +/- percentage, no distribution.
* Values are canonical-unit values already converted (and recorded) by the resolver; the record's own value and
  unit are carried alongside.
* Assumed / illustrative inputs have no evidence resolution and yield no scenario set. They are not varied by
  evidence challenges and are listed as limitations by the caller.
* Scenario ids are deterministic: ``<parameter>|<evidence_id>|<role>``; sets sort by evidence id then role.
"""

from __future__ import annotations

from collections.abc import Iterable

from ecobackfire.contracts.common import IntervalUncertainty
from ecobackfire.contracts.evidence_sensitivity import EvidenceRange, EvidenceScenario, EvidenceScenarioSet
from ecobackfire.contracts.resolution import InputResolution, ResolvedRecord

_ROLE_ORDER = {"lower": 0, "central": 1, "source": 1, "upper": 2}


def source_key(rec: ResolvedRecord) -> str:
    """Two records with the same key are ONE source (e.g. two years from the same publication)."""
    return rec.source.source_id if rec.source is not None and rec.source.source_id else rec.evidence_id


def _scenario(
    rec: ResolvedRecord,
    parameter: str,
    canon_unit: str,
    role: str,
    value: float,
    original: float | None,
    original_unit: str | None,
    baseline_id: str | None,
) -> EvidenceScenario:
    return EvidenceScenario(
        scenario_id=f"{parameter}|{rec.evidence_id}|{role}",
        parameter=parameter,
        role=role,  # type: ignore[arg-type]
        value=value,
        unit=canon_unit,
        original_value=original,
        original_unit=original_unit,
        evidence_id=rec.evidence_id,
        source_id=rec.source.source_id if rec.source is not None else None,
        source_key=source_key(rec),
        dataset_id=rec.dataset_id,
        dataset_version=rec.version,
        geography=rec.geography_text,
        period=rec.period_text,
        provenance_class=rec.provenance_class.value,
        status=rec.status,
        release_content_sha256=rec.release_content_sha256,
        is_baseline=(baseline_id is not None and rec.evidence_id == baseline_id and role in ("source", "central")),
    )


def record_range(rec: ResolvedRecord, parameter: str, canon_unit: str) -> EvidenceRange | None:
    """The documented range of one record, or None when the record documents none (nothing is invented)."""
    if rec.range_canonical is None or not isinstance(rec.uncertainty, IntervalUncertainty):
        return None
    lo, hi = rec.range_canonical
    if not lo < hi:
        return None  # a degenerate interval is a point, not a range
    central = rec.conversion.canonical_value
    return EvidenceRange(
        parameter=parameter,
        unit=canon_unit,
        lower=lo,
        upper=hi,
        central=central if lo <= central <= hi else None,
        evidence_id=rec.evidence_id,
        source_id=rec.source.source_id if rec.source is not None else None,
        dataset_id=rec.dataset_id,
        dataset_version=rec.version,
        geography=rec.geography_text,
        period=rec.period_text,
        provenance_class=rec.provenance_class.value,
    )


def build_scenario_set(
    resolution: InputResolution, canonical_unit: str, *, baseline_evidence_id: str | None = None
) -> EvidenceScenarioSet | None:
    """Scenarios for one evidence-requested input; ``None`` for an assumed/illustrative input.

    ``baseline_evidence_id`` defaults to the resolution's selected record (None for a conflicting resolution).
    """
    if resolution.basis != "evidence" or resolution.state is None:
        return None
    parameter = resolution.variable_id
    baseline = baseline_evidence_id or (resolution.selected.evidence_id if resolution.selected else None)
    if resolution.state == "missing":
        why = "; ".join(f"{e.evidence_id}: {r.code}" for e in resolution.excluded for r in e.reasons)
        return EvidenceScenarioSet(
            parameter=parameter,
            unit=canonical_unit,
            resolution_state="missing",
            resolution_decision="no applicable record" + (f" ({why})" if why else ""),
            baseline_evidence_id=None,
            warnings=["evidence is missing for this input: no scenario can be built and none is invented"],
        )
    decision = (
        resolution.selection_rule
        or "conflicting: no record was chosen on the caller's behalf; every applicable record is an alternative"
    )
    scenarios: list[EvidenceScenario] = []
    ranges: list[EvidenceRange] = []
    for rec in sorted(resolution.applicable, key=lambda r: r.evidence_id):
        rng = record_range(rec, parameter, canonical_unit)
        if rng is None:
            scenarios.append(
                _scenario(
                    rec, parameter, canonical_unit, "source",
                    rec.conversion.canonical_value, rec.value, rec.unit, baseline,
                )
            )
            continue
        ranges.append(rng)
        iu = rec.uncertainty
        assert isinstance(iu, IntervalUncertainty)
        scenarios.append(_scenario(rec, parameter, canonical_unit, "lower", rng.lower, iu.low, rec.unit, baseline))
        if rng.central is not None:
            scenarios.append(
                _scenario(rec, parameter, canonical_unit, "central", rng.central, rec.value, rec.unit, baseline)
            )
        scenarios.append(_scenario(rec, parameter, canonical_unit, "upper", rng.upper, iu.high, rec.unit, baseline))
    scenarios.sort(key=lambda s: (s.evidence_id, _ROLE_ORDER[s.role]))
    keys = sorted({s.source_key for s in scenarios})
    excluded = [f"{e.evidence_id}: " + "; ".join(r.code for r in e.reasons) for e in resolution.excluded]
    warnings = list(resolution.warnings)
    if not scenarios:
        warnings.append("no applicable record: no scenario can be built")
    return EvidenceScenarioSet(
        parameter=parameter,
        unit=canonical_unit,
        resolution_state=resolution.state,
        resolution_decision=decision,
        baseline_evidence_id=baseline,
        scenarios=scenarios,
        ranges=ranges,
        source_keys=keys,
        excluded=excluded,
        warnings=warnings,
    )


def build_scenario_sets(
    resolutions: Iterable[InputResolution], canonical_units: dict[str, str]
) -> list[EvidenceScenarioSet]:
    """One set per evidence-requested input (sorted by parameter). Unknown units raise ``KeyError`` loudly."""
    out = []
    for r in sorted(resolutions, key=lambda x: x.variable_id):
        s = build_scenario_set(r, canonical_units[r.variable_id])
        if s is not None:
            out.append(s)
    return out
