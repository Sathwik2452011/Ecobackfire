"""Local, offline evidence store: a validated collection of EvidenceRecords loaded from one JSON file.

File format::

    {"schema_version": "1.0.0", "records": [ {EvidenceRecord}, ... ]}

No network access. No records ship with the package: a store contains only what a human put in it.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import ValidationError

from ecobackfire.contracts.common import ProvenanceClass, ValidationIssue
from ecobackfire.contracts.experiment import Binding
from ecobackfire.contracts.model import EnvironmentalModel
from ecobackfire.core.hashing import canonical_json, sha256_text
from ecobackfire.errors import EvidenceValidationError, MissingEvidenceError, UnitError
from ecobackfire.evidence.records import (
    EVIDENCE_SCHEMA_VERSION,
    EvidenceRecord,
    EvidenceTier,
    classify_tier,
)
from ecobackfire.units import to_canonical

_VALUE_RTOL = 1e-9


class EvidenceStore:
    def __init__(self, records: Sequence[EvidenceRecord] = ()) -> None:
        by_id: dict[str, EvidenceRecord] = {}
        for r in records:
            if r.record_id in by_id:
                raise EvidenceValidationError(f"duplicate evidence record_id {r.record_id!r}")
            by_id[r.record_id] = r
        self._records = by_id

    # ------------------------------------------------------------------ loading
    @classmethod
    def from_file(cls, path: str | Path) -> EvidenceStore:
        p = Path(path)
        try:
            text = p.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise EvidenceValidationError(f"evidence file not found: {p}") from None
        if not text.strip():
            raise EvidenceValidationError(f"evidence file is empty: {p}")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise EvidenceValidationError(f"invalid JSON in {p}: {exc}") from exc
        if not isinstance(data, dict) or set(data) != {"schema_version", "records"}:
            raise EvidenceValidationError(
                f"{p}: top level must be an object with exactly the keys 'schema_version' and 'records'"
            )
        if data["schema_version"] != EVIDENCE_SCHEMA_VERSION:
            raise EvidenceValidationError(
                f"{p}: unsupported evidence schema_version {data['schema_version']!r}; "
                f"this build reads {EVIDENCE_SCHEMA_VERSION!r}"
            )
        if not isinstance(data["records"], list):
            raise EvidenceValidationError(f"{p}: 'records' must be a list")
        records: list[EvidenceRecord] = []
        for i, raw in enumerate(data["records"]):
            try:
                records.append(EvidenceRecord.model_validate(raw))
            except ValidationError as exc:
                raise EvidenceValidationError(f"{p}: record #{i} is invalid:\n{exc}") from exc
        return cls(records)

    # ------------------------------------------------------------------ access
    def __len__(self) -> int:
        return len(self._records)

    def ids(self) -> list[str]:
        return sorted(self._records)

    def get(self, record_id: str) -> EvidenceRecord:
        try:
            return self._records[record_id]
        except KeyError:
            raise MissingEvidenceError(f"evidence record {record_id!r} not found in store") from None

    def records_for(self, variable_id: str) -> list[EvidenceRecord]:
        return [r for _, r in sorted(self._records.items()) if r.variable_id == variable_id]

    def sha256(self) -> str:
        """Content hash independent of record order (for manifests)."""
        payload = [self._records[k].model_dump(mode="json") for k in sorted(self._records)]
        return sha256_text(canonical_json({"schema_version": EVIDENCE_SCHEMA_VERSION, "records": payload}))

    # ------------------------------------------------------------------ binding consistency
    def check_binding(self, vid: str, binding: Binding, canonical_unit: str) -> list[ValidationIssue]:
        """Issues if ``binding`` claims more than the store supports. Does not mutate or execute anything."""

        def issue(sev: str, code: str, msg: str) -> ValidationIssue:
            return ValidationIssue(
                severity=sev,  # type: ignore[arg-type]
                tier="schema",
                code=code,
                message=msg,
                path=f"bindings.{vid}",
            )

        cited = binding.provenance_class in (ProvenanceClass.MEASURED, ProvenanceClass.SOURCED)
        if binding.evidence_record_id is None:
            if cited:  # unreachable via Binding validation, kept as defence in depth
                return [issue("error", "E_NO_RECORD", "measured/sourced binding has no evidence_record_id")]
            return []
        try:
            rec = self.get(binding.evidence_record_id)
        except MissingEvidenceError:
            missing = f"evidence record {binding.evidence_record_id!r} not in store"
            return [issue("error", "E_RECORD_NOT_FOUND", missing)]
        out: list[ValidationIssue] = []
        if rec.variable_id != vid:
            out.append(issue("error", "E_VARIABLE_MISMATCH", f"record supports {rec.variable_id!r}, not {vid!r}"))
        if rec.provenance_class is not binding.provenance_class:
            out.append(
                issue(
                    "error",
                    "E_CLASS_MISMATCH",
                    f"binding says '{binding.provenance_class.value}' but record is '{rec.provenance_class.value}'",
                )
            )
        if binding.missing or binding.value is None:
            out.append(issue("error", "E_BINDING_MISSING_VALUE", "binding references a record but carries no value"))
            return out
        try:
            rec_c = to_canonical(rec.value, rec.unit, canonical_unit, what=f"record {rec.record_id}")
            bnd_c = to_canonical(binding.value, binding.unit, canonical_unit, what=f"binding {vid}")
        except UnitError as exc:
            out.append(issue("error", "E_UNIT", str(exc)))
            return out
        if not math.isclose(rec_c, bnd_c, rel_tol=_VALUE_RTOL, abs_tol=1e-300):
            msg = f"binding value {bnd_c:g} differs from record value {rec_c:g} (canonical units)"
            out.append(issue("error", "E_VALUE_MISMATCH", msg))
        if cited and classify_tier(rec) is EvidenceTier.CITED_UNREVIEWED:
            out.append(issue("warning", "W_UNREVIEWED", "cited record has not been reviewed by a named human"))
        return out

    def check_bindings(self, bindings: Mapping[str, Binding], spec: EnvironmentalModel) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for vid in sorted(bindings):
            issues += self.check_binding(vid, bindings[vid], spec.input_by_id(vid).canonical_unit)
        return issues
