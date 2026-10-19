"""Local, machine-readable dataset registry (no database, no network).

``DatasetRegistry.from_directory(root)`` scans ``<root>/<dataset_id>/<release_version>/dataset.json`` (see
``layout``), validates every release with ``DatasetRelease``, and answers: which datasets / versions / variables
exist, what they cover, which sources support them, what uncertainty each value carries, and which model
parameters they could provide.

Nothing is silently chosen: ``get`` needs an explicit version (``latest_version`` is a separate, visible call),
conflicting or missing evidence is NOT decided here (parameter resolution is Step 2/3), and a malformed release
is an error naming the file, never a skipped entry.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ecobackfire.contracts.model import EnvironmentalModel
from ecobackfire.core.hashing import canonical_json, sha256_text
from ecobackfire.datasets import geography, temporal
from ecobackfire.datasets.layout import DatasetLocation, discover, sha256_file
from ecobackfire.datasets.schema import USABLE_BY_DEFAULT, DatasetRecord, DatasetRelease, QualityFlag
from ecobackfire.datasets.status import EvidenceStatus
from ecobackfire.datasets.versioning import latest_version, parse_version, sort_versions
from ecobackfire.errors import DatasetNotFoundError, DatasetValidationError, UnitError
from ecobackfire.evidence import EvidenceStore
from ecobackfire.evidence.records import EvidenceRecord
from ecobackfire.units import to_canonical


def qualified_id(dataset_id: str, version: str, record_id: str) -> str:
    """Globally unique evidence id: ``<dataset_id>@<version>:<record_id>`` (record ids repeat across versions)."""
    return f"{dataset_id}@{version}:{record_id}"


@dataclass(frozen=True)
class LoadedRelease:
    release: DatasetRelease
    path: Path
    file_sha256: str  # SHA-256 of the dataset.json bytes exactly as read
    content_sha256: str  # SHA-256 of the canonical JSON of the validated release (formatting-independent)


@dataclass(frozen=True)
class RegistryEntry:
    """One record viewed through the registry (the unit a resolver will consider)."""

    evidence_id: str
    dataset_id: str
    version: str
    record: DatasetRecord
    status: EvidenceStatus
    quality: QualityFlag
    methodology: str | None

    @property
    def variable_id(self) -> str:
        return self.record.variable_id

    @property
    def usable_by_default(self) -> bool:
        return self.quality in USABLE_BY_DEFAULT

    def to_dict(self) -> dict[str, Any]:
        e = self.record.evidence
        return {
            "evidence_id": self.evidence_id,
            "dataset_id": self.dataset_id,
            "version": self.version,
            "variable_id": e.variable_id,
            "value": e.value,
            "unit": e.unit,
            "uncertainty": e.uncertainty.model_dump(mode="json"),
            "provenance_class": e.provenance_class.value,
            "status": self.status.value,
            "reviewed": e.review is not None,
            "quality": self.quality.value,
            "source": e.source.model_dump(mode="json") if e.source else None,
            "geography": e.scope.geography,
            "period": e.scope.period,
            "geography_codes": list(self.record.geography_codes),
            "temporal": self.record.temporal.model_dump(mode="json") if self.record.temporal else None,
            "methodology": self.methodology,
            "derived_from": list(self.record.derived_from),
            "derivation": self.record.derivation,
        }


@dataclass(frozen=True)
class ParameterCandidate:
    """A registry entry that names a model input. ``eligible`` says whether it may be offered to that input."""

    variable_id: str
    evidence_id: str
    status: EvidenceStatus
    unit: str
    canonical_unit: str
    eligible: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "variable_id": self.variable_id,
            "evidence_id": self.evidence_id,
            "status": self.status.value,
            "unit": self.unit,
            "canonical_unit": self.canonical_unit,
            "eligible": self.eligible,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ParameterAvailability:
    model_ref: str
    candidates: dict[str, tuple[ParameterCandidate, ...]]  # model parameter -> candidates (may be ineligible)
    unprovided: tuple[str, ...]  # model parameters with no registry entry at all
    documented_missing: dict[str, tuple[str, ...]]  # parameter -> "<dataset>@<version>: reason" notes

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_ref": self.model_ref,
            "candidates": {k: [c.to_dict() for c in v] for k, v in sorted(self.candidates.items())},
            "unprovided": list(self.unprovided),
            "documented_missing": {k: list(v) for k, v in sorted(self.documented_missing.items())},
        }


class DatasetRegistry:
    def __init__(
        self,
        releases: Iterable[LoadedRelease] = (),
        *,
        root: Path | None = None,
        load_errors: Iterable[str] = (),
    ) -> None:
        by_key: dict[tuple[str, str], LoadedRelease] = {}
        for lr in releases:
            key = (lr.release.dataset_id, lr.release.release_version)
            if key in by_key:
                raise DatasetValidationError(f"duplicate release {key[0]}@{key[1]}")
            by_key[key] = lr
        self._releases = by_key
        self.root = root
        self.load_errors: tuple[str, ...] = tuple(load_errors)

    # ------------------------------------------------------------------ loading
    @classmethod
    def from_directory(cls, root: str | Path, *, strict: bool = True) -> DatasetRegistry:
        """Load every release under ``root``. ``strict`` raises one error listing ALL problems; with
        ``strict=False`` problems are kept in ``load_errors`` (the bad releases are absent, visibly)."""
        root_p = Path(root)
        try:
            locations, problems = discover(root_p)
        except FileNotFoundError as exc:
            raise DatasetValidationError(str(exc)) from None
        loaded: list[LoadedRelease] = []
        for loc in locations:
            try:
                loaded.append(_load_release(loc))
            except DatasetValidationError as exc:
                problems.append(str(exc))
        if strict and problems:
            raise DatasetValidationError(f"{len(problems)} problem(s) loading {root_p}:\n" + "\n".join(problems))
        return cls(loaded, root=root_p, load_errors=problems)

    # ------------------------------------------------------------------ catalogue
    def dataset_ids(self) -> list[str]:
        return sorted({k[0] for k in self._releases})

    def versions(self, dataset_id: str) -> list[str]:
        vs = [k[1] for k in self._releases if k[0] == dataset_id]
        if not vs:
            raise DatasetNotFoundError(f"dataset {dataset_id!r} not found in registry")
        return sort_versions(vs)

    def latest_version(self, dataset_id: str) -> str:
        return latest_version(self.versions(dataset_id))

    def get(self, dataset_id: str, version: str) -> LoadedRelease:
        """Explicit version required: there is no implicit 'latest'."""
        try:
            return self._releases[(dataset_id, version)]
        except KeyError:
            raise DatasetNotFoundError(f"dataset {dataset_id}@{version} not found in registry") from None

    def __len__(self) -> int:
        return len(self._releases)

    def releases(self) -> list[LoadedRelease]:
        return [self._releases[k] for k in sorted(self._releases, key=lambda k: (k[0], parse_version(k[1])))]

    # ------------------------------------------------------------------ entries
    def entries(
        self,
        *,
        variable_id: str | None = None,
        dataset_id: str | None = None,
        version: str | None = None,
        usable_only: bool = False,
    ) -> list[RegistryEntry]:
        out: list[RegistryEntry] = []
        for lr in self.releases():
            rel = lr.release
            if dataset_id is not None and rel.dataset_id != dataset_id:
                continue
            if version is not None and rel.release_version != version:
                continue
            for rec in rel.records:
                if variable_id is not None and rec.variable_id != variable_id:
                    continue
                q = rel.effective_quality(rec)
                if usable_only and q not in USABLE_BY_DEFAULT:
                    continue
                out.append(
                    RegistryEntry(
                        evidence_id=qualified_id(rel.dataset_id, rel.release_version, rec.record_id),
                        dataset_id=rel.dataset_id,
                        version=rel.release_version,
                        record=rec,
                        status=rec.status(),
                        quality=q,
                        methodology=rel.effective_methodology(rec),
                    )
                )
        return out

    def entry(self, evidence_id: str) -> RegistryEntry:
        for e in self.entries():
            if e.evidence_id == evidence_id:
                return e
        raise DatasetNotFoundError(f"evidence id {evidence_id!r} not found in registry")

    def variables(self, dataset_id: str, version: str) -> list[str]:
        rel = self.get(dataset_id, version).release
        return sorted({r.variable_id for r in rel.records})

    def all_variables(self) -> list[str]:
        return sorted({e.variable_id for e in self.entries()})

    def sources(self, dataset_id: str, version: str) -> list[dict[str, Any]]:
        """Distinct sources cited by the release's records (by source_id), with the variables they support."""
        rel = self.get(dataset_id, version).release
        by_src: dict[str, dict[str, Any]] = {}
        for r in rel.records:
            src = r.evidence.source
            if src is None:
                continue
            d = by_src.setdefault(src.source_id, {"source": src.model_dump(mode="json"), "variables": set()})
            d["variables"].add(r.variable_id)
        return [{"source": d["source"], "variables": sorted(d["variables"])} for _, d in sorted(by_src.items())]

    def coverage(self, dataset_id: str, version: str) -> dict[str, Any]:
        """Geographic/temporal coverage as documented. Records lacking structured scope are counted, not guessed."""
        rel = self.get(dataset_id, version).release
        codes: set[str] = set()
        periods = []
        free_geo: set[str] = set()
        free_period: set[str] = set()
        no_codes = no_temporal = 0
        for r in rel.records:
            codes.update(r.geography_codes)
            no_codes += 0 if r.geography_codes else 1
            if r.temporal is not None:
                periods.append(r.temporal.period)
            else:
                no_temporal += 1
            if r.evidence.scope.geography:
                free_geo.add(r.evidence.scope.geography)
            if r.evidence.scope.period:
                free_period.add(r.evidence.scope.period)
        hull = temporal.hull(periods)
        return {
            "geography_codes": sorted(codes),
            "geography_text": sorted(free_geo),
            "period_hull": None if hull is None else {"start": hull.start.isoformat(), "end": hull.end.isoformat()},
            "period_text": sorted(free_period),
            "n_records": len(rel.records),
            "n_records_without_geography_codes": no_codes,
            "n_records_without_temporal_coverage": no_temporal,
        }

    def covers_geography(self, entry: RegistryEntry, requested_code: str) -> bool | None:
        """True/False if the record has structured codes; None (unknown) if it has none. Never guessed."""
        codes = entry.record.geography_codes
        return geography.covers_any(codes, requested_code) if codes else None

    # ------------------------------------------------------------------ describe / index
    def describe(self, dataset_id: str, version: str) -> dict[str, Any]:
        lr = self.get(dataset_id, version)
        rel = lr.release
        return {
            "dataset_id": rel.dataset_id,
            "release_version": rel.release_version,
            "title": rel.title,
            "description": rel.description,
            "provider": rel.provider,
            "methodology": rel.methodology,
            "license": rel.license.model_dump(mode="json") if rel.license else None,
            "last_updated": rel.last_updated,
            "quality": rel.quality.value,
            "parent_release": rel.parent_release,
            "changelog": rel.changelog,
            "metadata": dict(rel.metadata),
            "path": str(lr.path),
            "file_sha256": lr.file_sha256,
            "content_sha256": lr.content_sha256,
            "raw_files": [f.model_dump(mode="json") for f in rel.raw_files],
            "missing_variables": [m.model_dump(mode="json") for m in rel.missing_variables],
            "variables": self.variables(dataset_id, version),
            "sources": self.sources(dataset_id, version),
            "coverage": self.coverage(dataset_id, version),
            "entries": [e.to_dict() for e in self.entries(dataset_id=dataset_id, version=version)],
        }

    def index(self) -> dict[str, Any]:
        """Compact, JSON-serialisable catalogue of everything in the registry."""
        datasets: dict[str, Any] = {}
        for lr in self.releases():
            rel = lr.release
            datasets.setdefault(rel.dataset_id, {})[rel.release_version] = {
                "title": rel.title,
                "quality": rel.quality.value,
                "last_updated": rel.last_updated,
                "n_records": len(rel.records),
                "variables": self.variables(rel.dataset_id, rel.release_version),
                "file_sha256": lr.file_sha256,
                "content_sha256": lr.content_sha256,
            }
        return {"registry_sha256": self.sha256(), "datasets": datasets, "load_errors": list(self.load_errors)}

    def sha256(self) -> str:
        """Hash over (dataset id, version, file hash) of every release; independent of discovery order."""
        items = [[k[0], k[1], self._releases[k].file_sha256] for k in sorted(self._releases)]
        return sha256_text(canonical_json(items))

    # ------------------------------------------------------------------ model parameters
    def parameter_availability(self, spec: EnvironmentalModel) -> ParameterAvailability:
        """Which of the model's parameters have registry entries, and may they legitimately be offered?

        Matching is by exact variable id. An entry is ineligible (reason given, still listed) if its unit cannot
        be converted to the input's canonical unit, or its quality flag is deprecated/withdrawn. Eligibility is
        NOT a statement that the value is right for the requested geography/period; that is the resolver's job.
        """
        cands: dict[str, tuple[ParameterCandidate, ...]] = {}
        unprovided: list[str] = []
        doc_missing: dict[str, list[str]] = {}
        params = [v for v in spec.inputs if v.kind == "parameter"]
        for var in params:
            found = []
            for e in self.entries(variable_id=var.id):
                reasons: list[str] = []
                try:
                    to_canonical(1.0, e.record.evidence.unit, var.canonical_unit, what=e.evidence_id)
                except UnitError as exc:
                    reasons.append(f"unit: {exc}")
                if not e.usable_by_default:
                    reasons.append(f"quality flag is '{e.quality.value}'")
                found.append(
                    ParameterCandidate(
                        variable_id=var.id,
                        evidence_id=e.evidence_id,
                        status=e.status,
                        unit=e.record.evidence.unit,
                        canonical_unit=var.canonical_unit,
                        eligible=not reasons,
                        reasons=tuple(reasons),
                    )
                )
            if found:
                cands[var.id] = tuple(found)
            else:
                unprovided.append(var.id)
            for lr in self.releases():
                for m in lr.release.missing_variables:
                    if m.variable_id == var.id:
                        doc_missing.setdefault(var.id, []).append(
                            f"{lr.release.dataset_id}@{lr.release.release_version}: {m.reason}"
                        )
        return ParameterAvailability(
            model_ref=spec.ref,
            candidates=cands,
            unprovided=tuple(unprovided),
            documented_missing={k: tuple(v) for k, v in doc_missing.items()},
        )

    # ------------------------------------------------------------------ bridge to the evidence store
    def evidence_store(self, *, usable_only: bool = True) -> EvidenceStore:
        """An ``EvidenceStore`` over registry records, keyed by qualified evidence id, so the existing
        ``check_binding`` works unchanged on dataset-backed bindings."""
        recs: list[EvidenceRecord] = [
            e.record.evidence.model_copy(update={"record_id": e.evidence_id})
            for e in self.entries(usable_only=usable_only)
        ]
        return EvidenceStore(recs)


def _load_release(loc: DatasetLocation) -> LoadedRelease:
    try:
        raw = loc.path.read_bytes()
    except OSError as exc:
        raise DatasetValidationError(f"{loc.path}: cannot read: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetValidationError(f"{loc.path}: invalid JSON: {exc}") from exc
    try:
        rel = DatasetRelease.model_validate(data)
    except ValidationError as exc:
        raise DatasetValidationError(f"{loc.path}: invalid dataset release:\n{exc}") from exc
    if rel.dataset_id != loc.dataset_id or rel.release_version != loc.version:
        raise DatasetValidationError(
            f"{loc.path}: file declares {rel.dataset_id}@{rel.release_version} but is stored under "
            f"{loc.dataset_id}/{loc.version}/ (copy error or tampering)"
        )
    content = sha256_text(canonical_json(rel.model_dump(mode="json")))
    return LoadedRelease(release=rel, path=loc.path, file_sha256=sha256_file(loc.path), content_sha256=content)
