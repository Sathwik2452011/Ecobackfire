"""Dataset release schema (pydantic). One ``dataset.json`` = one immutable ``DatasetRelease``.

Reuse, not duplication:
* every value is an existing ``EvidenceRecord`` (value, unit, uncertainty, provenance class, SourceRef, Scope,
  review) wrapped in a ``DatasetRecord`` that adds only dataset context (methodology, structured scope,
  quality flag, derivation, metadata);
* provenance classes, uncertainty types and ``Frozen`` come from ``contracts.common``.

This corresponds to ``DatasetManifest`` + records file in docs/data-contracts.md section 4, merged into one
file for the first implementation (deviation recorded in docs/datasets.md).

Nothing here fetches or verifies anything. A release contains only what a human put in it.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from ecobackfire.contracts.common import (
    PROVENANCE_STRENGTH_ORDER,
    Frozen,
    ProvenanceClass,
)
from ecobackfire.datasets import geography, temporal
from ecobackfire.datasets.layout import valid_dataset_id
from ecobackfire.datasets.status import EvidenceStatus, status_from_provenance
from ecobackfire.datasets.versioning import parse_version
from ecobackfire.evidence.records import EvidenceRecord, is_valid_url

DATASET_SCHEMA_VERSION = "1.0.0"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class QualityFlag(StrEnum):
    """Curator's standing for a release or a single record (default for resolution: see ``USABLE``)."""

    PROVISIONAL = "provisional"  # entered and structurally valid; not yet accepted by a curator
    ACCEPTED = "accepted"  # a curator accepts it for downstream use
    DEPRECATED = "deprecated"  # superseded or discouraged; used only on explicit request
    WITHDRAWN = "withdrawn"  # known to be wrong; never resolved


USABLE_BY_DEFAULT: frozenset[QualityFlag] = frozenset({QualityFlag.PROVISIONAL, QualityFlag.ACCEPTED})


class LicenseInfo(Frozen):
    identifier: str | None = None  # SPDX id or short name as stated by the provider
    text: str | None = None
    url: str | None = None
    #: None = unknown (never read as "allowed").
    redistribution_allowed: bool | None = None

    @model_validator(mode="after")
    def _check(self) -> LicenseInfo:
        if not (self.identifier or self.text or self.url):
            raise ValueError("license needs at least one of identifier, text, url")
        if self.url is not None and not is_valid_url(self.url):
            raise ValueError(f"license url {self.url!r} must be http(s)://... or doi:10.xxxx/...")
        return self


class TemporalCoverage(Frozen):
    """Reference period the value describes. ``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD``; partial dates span fully."""

    start: str
    end: str

    @model_validator(mode="after")
    def _check(self) -> TemporalCoverage:
        temporal.make_period(self.start, self.end)  # ValueError on bad or reversed periods
        return self

    @property
    def period(self) -> temporal.Period:
        return temporal.make_period(self.start, self.end)


class RawFile(Frozen):
    """A source file kept alongside the dataset, pinned by hash (verification is a later step)."""

    source_id: str
    path: str  # relative POSIX path under data/raw/
    sha256: str
    retrieved_on: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _check(self) -> RawFile:
        if not self.source_id.strip():
            raise ValueError("raw file needs a source_id")
        if not self.path or self.path.startswith("/") or "\\" in self.path or ".." in self.path.split("/"):
            raise ValueError(f"raw file path {self.path!r} must be a relative POSIX path without '..'")
        if not _SHA256_RE.match(self.sha256):
            raise ValueError("sha256 must be 64 lower-case hex characters")
        if self.retrieved_on is not None:
            temporal.parse_iso_date(self.retrieved_on, "retrieved_on")
        return self


class MissingVariable(Frozen):
    """An explicit, documented gap: this release knows the variable matters and has no usable value."""

    variable_id: str
    reason: str

    @model_validator(mode="after")
    def _check(self) -> MissingVariable:
        if not self.variable_id.strip() or not self.reason.strip():
            raise ValueError("missing variable needs a variable_id and a reason")
        return self


class DatasetRecord(Frozen):
    evidence: EvidenceRecord
    methodology: str | None = None  # measurement context; overrides the release-level text
    temporal: TemporalCoverage | None = None
    geography_codes: tuple[str, ...] = ()
    quality: QualityFlag | None = None  # None = inherit the release's flag
    #: Parent record ids (same release) and the documented procedure, for estimated/simulated values.
    derived_from: tuple[str, ...] = ()
    derivation: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("geography_codes")
    @classmethod
    def _geo(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        norm = tuple(geography.normalize_code(c) for c in v)
        if len(set(norm)) != len(norm):
            raise ValueError("duplicate geography codes")
        return norm

    @model_validator(mode="after")
    def _derivation_rules(self) -> DatasetRecord:
        cls = self.evidence.provenance_class
        if cls in (ProvenanceClass.MEASURED, ProvenanceClass.SOURCED):
            if self.derived_from or self.derivation:
                raise ValueError(f"{cls.value} records must not carry derived_from/derivation")
            return self
        if cls in (ProvenanceClass.ESTIMATED, ProvenanceClass.SIMULATED) and not (
            self.derivation and self.derivation.strip()
        ):
            raise ValueError(f"{cls.value} records require a documented derivation")
        if cls is ProvenanceClass.ESTIMATED and not self.derived_from:
            raise ValueError("estimated records require derived_from (parent record ids)")
        if self.evidence.record_id in self.derived_from:
            raise ValueError("a record cannot be derived from itself")
        if len(set(self.derived_from)) != len(self.derived_from):
            raise ValueError("duplicate derived_from ids")
        return self

    @property
    def record_id(self) -> str:
        return self.evidence.record_id

    @property
    def variable_id(self) -> str:
        return self.evidence.variable_id

    def status(self) -> EvidenceStatus:
        return status_from_provenance(self.evidence.provenance_class, reviewed=self.evidence.review is not None)


class DatasetRelease(Frozen):
    schema_version: str = DATASET_SCHEMA_VERSION
    dataset_id: str
    release_version: str
    title: str
    description: str
    provider: str  # organisation or person that published the underlying data (or the curator for assumptions)
    methodology: str | None = None  # release-level measurement/estimation context
    license: LicenseInfo | None = None
    last_updated: str  # ISO date of the last change to this release's content
    quality: QualityFlag = QualityFlag.PROVISIONAL
    parent_release: str | None = None
    changelog: str
    raw_files: tuple[RawFile, ...] = ()
    missing_variables: tuple[MissingVariable, ...] = ()
    metadata: dict[str, str] = Field(default_factory=dict)
    records: tuple[DatasetRecord, ...] = ()

    @model_validator(mode="after")
    def _release_integrity(self) -> DatasetRelease:
        if self.schema_version != DATASET_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported dataset schema_version {self.schema_version!r}; "
                f"this build reads {DATASET_SCHEMA_VERSION!r}"
            )
        if not valid_dataset_id(self.dataset_id):
            raise ValueError(f"invalid dataset_id {self.dataset_id!r}")
        version = parse_version(self.release_version)
        if self.parent_release is not None and not parse_version(self.parent_release) < version:
            raise ValueError("parent_release must be an earlier version than release_version")
        for name in ("title", "description", "provider", "changelog"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must be non-empty")
        temporal.parse_iso_date(self.last_updated, "last_updated")
        if not self.records and not self.missing_variables:
            raise ValueError("a release needs at least one record or one documented missing variable")
        ids = [r.record_id for r in self.records]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"duplicate record_id within release: {dup}")
        paths = [f.path for f in self.raw_files]
        if len(set(paths)) != len(paths):
            raise ValueError("duplicate raw_files path")
        present = {r.variable_id for r in self.records}
        both = sorted(present & {m.variable_id for m in self.missing_variables})
        if both:
            raise ValueError(f"variables listed as missing but also present in records: {both}")
        self._check_derivations({r.record_id: r for r in self.records})
        return self

    def _check_derivations(self, by_id: dict[str, DatasetRecord]) -> None:
        strength = PROVENANCE_STRENGTH_ORDER.index
        for r in self.records:
            for pid in r.derived_from:
                if pid not in by_id:
                    raise ValueError(f"record {r.record_id!r} derives from unknown record {pid!r}")
                # A derived value can never be stronger than its weakest parent (no promotion).
                if strength(r.evidence.provenance_class) < strength(by_id[pid].evidence.provenance_class):
                    raise ValueError(
                        f"record {r.record_id!r} ({r.evidence.provenance_class.value}) is stronger than its "
                        f"parent {pid!r} ({by_id[pid].evidence.provenance_class.value}); derivation cannot promote"
                    )
        # cycle check (iterative DFS, colours: 0 new, 1 on stack, 2 done)
        colour: dict[str, int] = dict.fromkeys(by_id, 0)
        for start in by_id:
            if colour[start]:
                continue
            stack = [(start, iter(by_id[start].derived_from))]
            colour[start] = 1
            while stack:
                node, it = stack[-1]
                nxt = next(it, None)
                if nxt is None:
                    colour[node] = 2
                    stack.pop()
                elif colour[nxt] == 1:
                    raise ValueError(f"derivation cycle involving {nxt!r}")
                elif colour[nxt] == 0:
                    colour[nxt] = 1
                    stack.append((nxt, iter(by_id[nxt].derived_from)))

    # ----------------------------------------------------------- effective (inherited) attributes
    def effective_quality(self, record: DatasetRecord) -> QualityFlag:
        """The record's own flag, else the release's. A withdrawn release withdraws all its records."""
        if self.quality is QualityFlag.WITHDRAWN:
            return QualityFlag.WITHDRAWN
        return record.quality or self.quality

    def effective_methodology(self, record: DatasetRecord) -> str | None:
        return record.methodology or self.methodology
