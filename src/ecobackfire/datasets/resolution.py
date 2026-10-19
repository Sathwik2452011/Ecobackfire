"""Evidence-backed parameter resolution (layer 2).

    model parameter -> dataset variable -> dataset version -> record (source/evidence) -> value + unit + scope

``resolve_parameter`` answers ONE question: for this model input, and this explicit request (dataset, exact
version, geography, period), which registry record may supply the value? It never guesses:

* Every record of the requested dataset version that names the variable is looked at. Records that are not used
  are listed in ``excluded`` with ALL reasons (unit, quality, status, physical bounds, geography, period).
* Geography must match exactly unless the request opts in to ``geography_fallback``; with fallback the most
  specific available tier wins (exact > broader > GLOBAL) and the fallback is a warning, never silent.
* A record without structured geography codes / temporal coverage cannot be matched and is excluded; scope is
  never inferred from free text.
* Several applicable records: if their canonical-unit uncertainty intervals (a point when none is stated) all
  overlap they are *corroborating*, and the strongest-origin record (then smallest id) is selected by a named rule
  with every other record preserved in ``applicable``. If any pair is disjoint the state is ``conflicting``: NO
  value is chosen, every record is preserved, and the caller must pick one with ``EvidenceRequest.select``
  (a pick over a real conflict is recorded as ``overridden_by_caller``) or evaluate alternatives.
* No applicable record -> ``missing``. There is no default, no illustrative fallback and no 'latest' version.

Units go through ``ecobackfire.units.to_canonical`` (pint). Incompatible units exclude the record. The record's
own value and unit are never overwritten; the conversion is recorded in ``ResolvedRecord.conversion`` and the
binding built from a resolution keeps the ORIGINAL unit (the engine converts it with the same function).

Conversion note: interval bounds are converted bound by bound, which is exact for the multiplicative units this
project uses (not for offset units such as degC).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ecobackfire.contracts.common import ILLUSTRATIVE_MARKER, IntervalUncertainty, ProvenanceClass
from ecobackfire.contracts.experiment import Binding
from ecobackfire.contracts.model import EnvironmentalModel
from ecobackfire.contracts.resolution import (
    ConflictReport,
    ConversionRecord,
    EvidenceRequest,
    EvidenceResolutionSummary,
    ExcludedReason,
    ExcludedRecord,
    GeographyMatch,
    InputResolution,
    ReleaseRef,
    ResolvedRecord,
    SourceSummary,
    TemporalMatch,
)
from ecobackfire.core.engine import check_physical_bounds
from ecobackfire.datasets import geography, temporal
from ecobackfire.datasets.registry import DatasetRegistry, RegistryEntry
from ecobackfire.datasets.schema import QualityFlag
from ecobackfire.datasets.status import EvidenceStatus, status_rank
from ecobackfire.errors import (
    ConfigError,
    DatasetNotFoundError,
    EvidenceConflictError,
    MissingEvidenceError,
    PhysicalConstraintError,
    UnitError,
)
from ecobackfire.evidence.records import evidence_backed_issues
from ecobackfire.units import to_canonical

#: Relative tolerance used when deciding whether two uncertainty intervals overlap (policy, recorded in reports).
OVERLAP_RTOL = 1e-9
CONFLICT_RULE = (
    "records conflict when the canonical-unit uncertainty intervals of any two applicable records are disjoint "
    f"(a record without a stated interval is a point; relative tolerance {OVERLAP_RTOL:g})"
)
AGREEING_SELECTION_RULE = "agreeing_records: strongest origin status, then smallest evidence id"
_GEO_TIER = {"exact": 0, "broader": 1, "global": 2}
_NOTE = (
    "A resolved input is the value of ONE dataset record under the stated request. It is evidence for the "
    "model input only to the extent the record's own status, scope and uncertainty support (see each record's "
    "status, evidence_backed flag and caveats); it is not a validated real-world claim."
)


# ------------------------------------------------------------------ small pure helpers
def _convert(value: float, unit: str, canonical_unit: str, what: str) -> ConversionRecord:
    canon = to_canonical(value, unit, canonical_unit, what=what)
    return ConversionRecord(
        original_value=value,
        original_unit=unit,
        canonical_value=canon,
        canonical_unit=canonical_unit,
        factor=None if value == 0 else canon / value,
        performed=unit.strip() != canonical_unit.strip(),
    )


def _canonical_interval(entry: RegistryEntry, canonical_unit: str) -> tuple[float, float] | None:
    u = entry.record.evidence.uncertainty
    if not isinstance(u, IntervalUncertainty):
        return None
    unit = entry.record.evidence.unit
    a = to_canonical(u.low, unit, canonical_unit, what=entry.evidence_id)
    b = to_canonical(u.high, unit, canonical_unit, what=entry.evidence_id)
    return (min(a, b), max(a, b))


def parse_request_scope(req: EvidenceRequest) -> tuple[str, temporal.Period]:
    """Validate and normalize the requested geography and period (ConfigError on malformed input)."""
    try:
        code = geography.normalize_code(req.geography)
    except ValueError as exc:
        raise ConfigError(f"evidence request: {exc}") from None
    try:
        start, _, end = req.period.partition("/")
        period = temporal.make_period(start, end or start)
    except ValueError as exc:
        raise ConfigError(f"evidence request: {exc}") from None
    return code, period


def _geo_match(entry: RegistryEntry, requested: str) -> GeographyMatch | None:
    best: GeographyMatch | None = None
    for c in entry.record.geography_codes:
        if c == requested:
            return "exact"
        if geography.covers(c, requested):
            tier: GeographyMatch = "global" if c == geography.GLOBAL else "broader"
            if best is None or _GEO_TIER[tier] < _GEO_TIER[best]:
                best = tier
    return best


def _intervals_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    scale = max(abs(a[0]), abs(a[1]), abs(b[0]), abs(b[1]), 1e-300)
    tol = OVERLAP_RTOL * scale
    return a[0] <= b[1] + tol and b[0] <= a[1] + tol


def _basis_for(record: ResolvedRecord) -> str:
    if record.status == "illustrative":
        return "illustrative"
    if record.status == "assumption":
        return "assumed"
    return "evidence"


# ------------------------------------------------------------------ the resolver
def resolve_parameter(
    registry: DatasetRegistry, spec: EnvironmentalModel, variable_id: str, request: EvidenceRequest
) -> InputResolution:
    """Resolve one model input against the registry. Returns resolved / conflicting / missing; never guesses."""
    try:
        var = spec.input_by_id(variable_id)
    except KeyError:
        raise ConfigError(
            f"evidence request for unknown model input {variable_id!r}; inputs: {sorted(v.id for v in spec.inputs)}"
        ) from None
    if var.kind != "parameter":
        raise ConfigError(f"{variable_id}: only model parameters can be resolved from evidence (kind={var.kind!r})")
    canon_unit = var.canonical_unit
    req_geo, req_period = parse_request_scope(request)
    log: list[str] = [
        f"request: {variable_id} from {request.dataset_id}@{request.version}, geography {req_geo}, "
        f"period {req_period.label()}, temporal_match={request.temporal_match}, "
        f"geography_fallback={request.geography_fallback}"
        + (f", minimum_status={request.minimum_status}" if request.minimum_status else "")
        + (f", select={request.select}" if request.select else "")
    ]

    def result(state: str, **kw: object) -> InputResolution:
        return InputResolution(
            variable_id=variable_id, basis="evidence", state=state, request=request, decision_log=log, **kw
        )  # type: ignore[arg-type]

    try:
        lr = registry.get(request.dataset_id, request.version)
    except DatasetNotFoundError as exc:
        log.append(f"dataset version not found in the registry: {exc}")
        gone = ExcludedRecord(
            evidence_id=f"{request.dataset_id}@{request.version}",
            reasons=[ExcludedReason(code="dataset_version_not_found", detail=str(exc))],
        )
        return result("missing", excluded=[gone])

    entries = registry.entries(variable_id=variable_id, dataset_id=request.dataset_id, version=request.version)
    log.append(f"{len(entries)} record(s) in {request.dataset_id}@{request.version} name variable {variable_id!r}")
    reasons: dict[str, list[ExcludedReason]] = {}
    ok: list[tuple[RegistryEntry, GeographyMatch, TemporalMatch, ConversionRecord]] = []

    for e in entries:
        why: list[ExcludedReason] = []
        if e.quality is QualityFlag.WITHDRAWN:
            why.append(ExcludedReason(code="quality_withdrawn", detail="record or release is withdrawn"))
        elif e.quality is QualityFlag.DEPRECATED and not request.include_deprecated:
            why.append(
                ExcludedReason(code="quality_deprecated", detail="deprecated; set include_deprecated to allow it")
            )
        if request.minimum_status is not None and status_rank(e.status) > status_rank(
            EvidenceStatus(request.minimum_status)
        ):
            why.append(
                ExcludedReason(
                    code="status_below_minimum",
                    detail=f"status '{e.status.value}' is weaker than the requested minimum '{request.minimum_status}'",
                )
            )
        conv: ConversionRecord | None = None
        try:
            conv = _convert(e.record.evidence.value, e.record.evidence.unit, canon_unit, e.evidence_id)
            _canonical_interval(e, canon_unit)
        except UnitError as exc:
            conv = None
            why.append(ExcludedReason(code="unit_incompatible", detail=str(exc)))
        if conv is not None:
            try:
                check_physical_bounds(spec, {variable_id: conv.canonical_value})
            except PhysicalConstraintError as exc:
                why.append(ExcludedReason(code="violates_physical_bounds", detail=str(exc)))
        geo = _geo_match(e, req_geo) if e.record.geography_codes else None
        if not e.record.geography_codes:
            why.append(
                ExcludedReason(
                    code="no_structured_geography",
                    detail="record has no geography_codes; scope is never inferred from free text",
                )
            )
        elif geo is None:
            why.append(
                ExcludedReason(
                    code="geography_mismatch",
                    detail=f"record covers {list(e.record.geography_codes)}, request is {req_geo}",
                )
            )
        tmatch: TemporalMatch | None = None
        if e.record.temporal is None:
            why.append(
                ExcludedReason(
                    code="no_structured_temporal_scope",
                    detail="record has no temporal coverage; period is never inferred from free text",
                )
            )
        else:
            rp = e.record.temporal.period
            if temporal.contains(rp, req_period):
                tmatch = "contains"
            elif request.temporal_match == "overlaps" and temporal.overlaps(rp, req_period):
                tmatch = "overlaps"
            else:
                why.append(
                    ExcludedReason(
                        code="temporal_mismatch",
                        detail=f"record period {rp.label()} does not {request.temporal_match} requested "
                        f"{req_period.label()}",
                    )
                )
        if why:
            reasons[e.evidence_id] = why
        elif conv is not None and geo is not None and tmatch is not None:
            ok.append((e, geo, tmatch, conv))

    # geography tiers
    if ok:
        best = min(_GEO_TIER[g] for _, g, _, _ in ok)
        for e, g, _, _ in list(ok):
            if _GEO_TIER[g] == 0:
                continue
            if not request.geography_fallback:
                reasons.setdefault(e.evidence_id, []).append(
                    ExcludedReason(
                        code="geography_fallback_not_permitted",
                        detail=f"record covers {list(e.record.geography_codes)} (a '{g}' match for {req_geo}); "
                        "set geography_fallback=true to allow a broader record",
                    )
                )
            elif _GEO_TIER[g] > best:
                reasons.setdefault(e.evidence_id, []).append(
                    ExcludedReason(
                        code="less_specific_geography_superseded",
                        detail=f"a more specific record ('{next(k for k, v in _GEO_TIER.items() if v == best)}') "
                        "is applicable",
                    )
                )
        ok = [t for t in ok if t[0].evidence_id not in reasons]
    log.append(f"{len(ok)} applicable record(s) after unit, quality, status, bounds, geography and period rules")

    def build_excluded() -> list[ExcludedRecord]:
        return [ExcludedRecord(evidence_id=k, reasons=v) for k, v in sorted(reasons.items())]

    if not ok:
        log.append("no appropriate evidence: nothing is substituted (no default, no illustrative fallback)")
        return result("missing", excluded=build_excluded())

    applicable: list[ResolvedRecord] = []
    for e, geo, tm, conv in sorted(ok, key=lambda t: t[0].evidence_id):
        ev = e.record.evidence
        issues = evidence_backed_issues(ev)
        src = ev.source
        td = e.record.temporal
        applicable.append(
            ResolvedRecord(
                evidence_id=e.evidence_id,
                dataset_id=e.dataset_id,
                version=e.version,
                record_id=e.record.record_id,
                status=e.status.value,  # type: ignore[arg-type]
                provenance_class=ev.provenance_class,
                quality=e.quality.value,
                reviewed=ev.review is not None,
                evidence_backed=not issues,
                evidence_backed_issues=issues,
                value=ev.value,
                unit=ev.unit,
                conversion=conv,
                uncertainty=ev.uncertainty,
                range_canonical=_canonical_interval(e, canon_unit),
                source=None
                if src is None
                else SourceSummary(
                    source_id=src.source_id,
                    citation=src.citation,
                    url=src.url,
                    locator=src.locator,
                    retrieved_on=src.retrieved_on,
                ),
                geography_text=ev.scope.geography,
                geography_codes=list(e.record.geography_codes),
                period_text=ev.scope.period,
                temporal_start=td.start if td else None,
                temporal_end=td.end if td else None,
                geography_match=geo,
                temporal_match=tm,
                methodology=e.methodology,
                note=ev.note,
                release_file_sha256=lr.file_sha256,
                release_content_sha256=lr.content_sha256,
                record_metadata=dict(sorted(e.record.metadata.items())),
            )
        )
        conv_txt = (
            f"{conv.original_value:.10g} {conv.original_unit} -> {conv.canonical_value:.10g} {conv.canonical_unit}"
            if conv.performed
            else f"{conv.canonical_value:.10g} {conv.canonical_unit} (no conversion)"
        )
        log.append(
            f"applicable: {e.evidence_id} status={e.status.value} geography_match={geo} "
            f"temporal_match={tm} value {conv_txt}"
        )

    warnings: list[str] = []

    def pick(sel: ResolvedRecord, rule: str, conflict: ConflictReport | None = None) -> InputResolution:
        if sel.geography_match != "exact":
            warnings.append(
                f"geography fallback: {req_geo} was requested but the record covers {sel.geography_codes} "
                f"('{sel.geography_match}' match); the value may not describe the requested geography"
            )
        if not sel.evidence_backed:
            warnings.append("not evidence-backed: " + "; ".join(sel.evidence_backed_issues))
        if sel.range_canonical is None:
            warnings.append("the record states no uncertainty interval; no range is available and none is invented")
        if sel.note:
            warnings.append(f"record caveat: {sel.note}")
        log.append(f"selected {sel.evidence_id} by rule: {rule}")
        return result(
            "resolved",
            selected=sel,
            selection_rule=rule,
            applicable=applicable,
            excluded=build_excluded(),
            conflict=conflict,
            warnings=warnings,
        )

    by_id = {r.evidence_id: r for r in applicable}
    if request.select is not None and request.select not in by_id:
        reasons.setdefault(request.select, []).append(
            ExcludedReason(
                code="selected_record_not_applicable",
                detail="the caller selected a record that is not applicable under this request",
            )
        )
        log.append(f"caller selected {request.select}, which is not applicable: nothing is substituted")
        return result("missing", excluded=build_excluded(), applicable=applicable)

    intervals = {r.evidence_id: r.range_canonical or (r.conversion.canonical_value,) * 2 for r in applicable}
    ids = sorted(by_id)
    disagree = any(not _intervals_overlap(intervals[a], intervals[b]) for i, a in enumerate(ids) for b in ids[i + 1 :])
    values = {i: by_id[i].conversion.canonical_value for i in ids}
    vmax, vmin = max(values.values()), min(values.values())
    spread = (vmax - vmin) / max(abs(vmax), abs(vmin), 1e-300)

    if len(applicable) == 1:
        sel = applicable[0]
        rule = "caller_selected" if request.select == sel.evidence_id else "single_applicable_record"
        return pick(sel, rule)

    conflict = (
        ConflictReport(
            evidence_ids=ids,
            canonical_values=values,
            max_relative_spread=spread,
            rule=CONFLICT_RULE,
            overridden_by_caller=request.select is not None,
        )
        if disagree
        else None
    )
    if request.select is not None:
        if conflict is not None:
            warnings.append(
                f"applicable records disagree (relative spread {spread:.3g}); the caller selected "
                f"{request.select}. The other records are preserved in 'applicable'."
            )
        return pick(by_id[request.select], "caller_selected", conflict)
    if conflict is not None:
        log.append(
            f"CONFLICT among {len(ids)} applicable records (relative spread {spread:.3g}); no value is chosen. "
            "Select one with EvidenceRequest.select or evaluate the alternatives."
        )
        return result(
            "conflicting",
            applicable=applicable,
            excluded=build_excluded(),
            conflict=conflict,
            warnings=[f"{len(ids)} applicable records disagree beyond their documented uncertainty"],
        )
    sel = min(applicable, key=lambda r: (status_rank(EvidenceStatus(r.status)), r.evidence_id))
    warnings.append(
        f"{len(ids)} applicable records agree within their uncertainty; {sel.evidence_id} was selected by rule "
        f"'{AGREEING_SELECTION_RULE}'. The others are preserved in 'applicable'."
    )
    return pick(sel, AGREEING_SELECTION_RULE)


# ------------------------------------------------------------------ bindings
def binding_from_resolution(res: InputResolution) -> Binding:
    """The Binding a resolved input contributes: the record's OWN value, unit and uncertainty (unconverted)."""
    if res.state != "resolved" or res.selected is None:
        raise MissingEvidenceError(f"{res.variable_id}: cannot build a binding from a '{res.state}' resolution")
    r = res.selected
    note = f"resolved from dataset record {r.evidence_id}"
    if r.provenance_class is ProvenanceClass.ILLUSTRATIVE:
        note = f"{ILLUSTRATIVE_MARKER}; {note}"
    return Binding(
        value=r.value,
        unit=r.unit,
        uncertainty=r.uncertainty,
        provenance_class=r.provenance_class,
        evidence_record_id=r.evidence_id,
        note=note,
    )


def _same_binding(a: Binding, b: Binding) -> bool:
    da, db = a.model_dump(mode="json"), b.model_dump(mode="json")
    da.pop("note", None)
    db.pop("note", None)
    return da == db


def describe_unresolved(res: InputResolution) -> str:
    """Human-readable explanation of a missing/conflicting resolution, listing every record involved."""
    head = f"{res.variable_id}: evidence is {res.state}"
    if res.request is not None:
        head += f" for {res.request.dataset_id}@{res.request.version} ({res.request.geography}, {res.request.period})"
    lines = [head]
    if res.state == "conflicting" and res.conflict is not None:
        for eid in res.conflict.evidence_ids:
            lines.append(f"  - {eid}: {res.conflict.canonical_values[eid]:.10g} (canonical unit)")
        lines.append(
            "  choose one with [evidence_requests." + res.variable_id + '] select = "<evidence id>", '
            "or evaluate each alternative"
        )
    for ex in res.excluded:
        for rs in ex.reasons:
            lines.append(f"  - excluded {ex.evidence_id}: {rs.code}: {rs.detail}")
    return "\n".join(lines)


@dataclass(frozen=True)
class BindingResolution:
    bindings: dict[str, Binding]  # input bindings merged with the resolved evidence bindings
    resolutions: dict[str, InputResolution]  # every requested input, in whatever state
    verified_evidence: dict[str, str] = field(default_factory=dict)  # resolved inputs: id -> evidence id

    def unresolved(self) -> list[InputResolution]:
        return [r for _, r in sorted(self.resolutions.items()) if r.state != "resolved"]

    def raise_if_unresolved(self) -> None:
        bad = self.unresolved()
        if not bad:
            return
        text = "\n".join(describe_unresolved(r) for r in bad)
        if any(r.state == "conflicting" for r in bad):
            raise EvidenceConflictError(text)
        raise MissingEvidenceError(text)


def resolve_requests(
    registry: DatasetRegistry,
    spec: EnvironmentalModel,
    bindings: Mapping[str, Binding],
    requests: Mapping[str, EvidenceRequest],
) -> BindingResolution:
    """Resolve every request and merge the resulting bindings. A binding for the same input that does not equal
    what the registry resolves is an error (re-materializing an already materialized configuration is fine)."""
    merged = dict(bindings)
    resolutions: dict[str, InputResolution] = {}
    verified: dict[str, str] = {}
    for vid in sorted(requests):
        res = resolve_parameter(registry, spec, vid, requests[vid])
        resolutions[vid] = res
        if res.state != "resolved":
            continue
        new = binding_from_resolution(res)
        old = bindings.get(vid)
        if old is not None and not _same_binding(old, new):
            raise ConfigError(
                f"{vid}: the configuration has both a binding and an evidence request, and they disagree. "
                f"The registry resolves {new.evidence_record_id} = {new.value} {new.unit}; the binding says "
                f"{old.value} {old.unit} ({old.provenance_class.value}, {old.evidence_record_id!r}). Remove the "
                "binding (the request supplies it) or, if this is a stored configuration, the registry content "
                "has changed since it was written."
            )
        merged[vid] = new
        verified[vid] = new.evidence_record_id or ""
    return BindingResolution(merged, resolutions, verified)


def build_summary(
    registry: DatasetRegistry, bindings: Mapping[str, Binding], resolutions: Mapping[str, InputResolution]
) -> EvidenceResolutionSummary:
    """Per-input evidence/assumption table for a run. Every bound input is listed with its basis."""
    rows: list[InputResolution] = []
    releases: dict[tuple[str, str], ReleaseRef] = {}
    for vid, b in sorted(bindings.items()):
        if b.missing:
            continue
        res = resolutions.get(vid)
        if res is not None and res.state == "resolved" and res.selected is not None:
            sel = res.selected
            rows.append(res.model_copy(update={"basis": _basis_for(sel), "provenance_class": sel.provenance_class}))
            releases[(sel.dataset_id, sel.version)] = ReleaseRef(
                dataset_id=sel.dataset_id,
                version=sel.version,
                file_sha256=sel.release_file_sha256,
                content_sha256=sel.release_content_sha256,
            )
        else:
            basis = "illustrative" if b.provenance_class is ProvenanceClass.ILLUSTRATIVE else "assumed"
            rows.append(InputResolution(variable_id=vid, basis=basis, provenance_class=b.provenance_class))
    return EvidenceResolutionSummary(
        registry_sha256=registry.sha256(),
        releases_used=[releases[k] for k in sorted(releases)],
        inputs=rows,
        n_evidence=sum(r.basis == "evidence" for r in rows),
        n_assumed=sum(r.basis == "assumed" for r in rows),
        n_illustrative=sum(r.basis == "illustrative" for r in rows),
        evidence_backed_inputs=[r.variable_id for r in rows if r.selected is not None and r.selected.evidence_backed],
        inputs_without_evidence=[r.variable_id for r in rows if r.basis != "evidence"],
        note=_NOTE,
    )
