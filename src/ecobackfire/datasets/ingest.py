"""Import real, sourced tabular data into a dataset release (Evidence & Dataset Intelligence, real-data Step 1).

What this module does
---------------------
An *import spec* (TOML) names raw files (each pinned by SHA-256), the source citation, which table rows to
take, which columns map to which model variables, the unit the source declares, and the geography code for each
row. ``build_release`` turns that into a ``DatasetRelease`` and an import report; ``write_release`` stores it
under the local registry layout.

Guarantees (each is tested)
---------------------------
* **No value is transformed.** The value is the decimal text of the cell, parsed to float64. There is no scaling,
  rounding, unit conversion or imputation at import time. Unit conversion happens later, at parameter
  resolution, where it is recorded. The exact cell text is kept in the record metadata.
* **Every check is reported, nothing is half-written.** All problems found are collected and raised together as one
  ``DatasetValidationError``; on any problem no file is written.
* **A pinned raw file that does not match its hash is rejected.** A selected row that is missing, duplicated or
  has an empty / non-numeric / non-finite cell is rejected, never skipped.
* **Declared units are checked against the source.** The spec must repeat the unit text the source's codebook
  gives, verbatim; a mismatch is an error. If a target model is supplied the variable must exist, the unit must
  convert to the model's canonical unit, and the (converted) value must satisfy the model's physical bounds. The
  converted number is used only for this check and is not stored.
* **Deterministic.** Same spec + same raw bytes -> byte-identical ``dataset.json`` and ``import_report.json``.
  No clock, network or randomness is used. Record ids are ``<variable>.<geography>.<period>``.
* **Releases are append-only.** Writing over an existing release with different bytes is an error.

What it cannot do (stated, not hidden)
--------------------------------------
* It cannot verify that the raw file is the genuine upstream file; it pins what a human supplied. Upstream identity is
  carried as recorded metadata (commit, full-file hash) and checkable with the extraction script next to the raw files.
* It does not attach a review. Records come out as ``sourced`` + no review, i.e. status ``sourced_unreviewed``.
* It does not invent uncertainty. If the source gives none, the record says ``unknown``.
* Multi-line quoted CSV rows are not supported (rejected), so a record maps to one physical source line.
"""

from __future__ import annotations

import csv
import json
import math
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from ecobackfire.contracts.common import Frozen, ProvenanceClass
from ecobackfire.contracts.model import EnvironmentalModel
from ecobackfire.core.hashing import canonical_json, sha256_bytes, sha256_text
from ecobackfire.datasets.layout import DATASET_FILENAME, sha256_file, valid_dataset_id
from ecobackfire.datasets.schema import (
    DatasetRecord,
    DatasetRelease,
    LicenseInfo,
    QualityFlag,
    RawFile,
    TemporalCoverage,
)
from ecobackfire.errors import ConfigError, DatasetValidationError, UnitError
from ecobackfire.evidence.records import EvidenceRecord, Scope, SourceRef
from ecobackfire.units import to_canonical, validate_unit_string

IMPORT_SPEC_SCHEMA_VERSION = "1.0.0"
IMPORTER_VERSION = "1"
REPORT_FILENAME = "import_report.json"
_DECIMAL_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")


# ---------------------------------------------------------------------------------------------- spec
class RawFileSpec(Frozen):
    key: str  # short handle used by [table] and [codebook]
    source_id: str
    path: str  # relative POSIX path under the raw root (default data/raw/)
    sha256: str
    retrieved_on: str | None = None
    note: str | None = None


class SourceSpec(Frozen):
    source_id: str
    citation: str
    url: str
    retrieved_on: str


class TableSpec(Frozen):
    file: str  # raw file key
    label_column: str
    period_column: str
    iso3_column: str | None = None


class CodebookSpec(Frozen):
    file: str  # raw file key
    key_column: str
    unit_column: str


class VariableSpec(Frozen):
    column: str
    variable_id: str
    unit: str
    #: The unit text the SOURCE's codebook gives for this column, verbatim. Must equal the codebook cell.
    codebook_unit_text: str
    description: str
    system_description: str | None = None
    note: str | None = None


class SelectSpec(Frozen):
    where: dict[str, str]  # column -> exact cell text; must match exactly one row
    geography_code: str
    geography_label: str
    source_iso3: str | None = None  # if set, must equal the row's iso3 cell ("" = the row has none)


class ImportSpec(Frozen):
    schema_version: str = IMPORT_SPEC_SCHEMA_VERSION
    dataset_id: str
    release_version: str
    title: str
    description: str
    provider: str
    methodology: str | None = None
    license: LicenseInfo | None = None
    last_updated: str
    quality: QualityFlag = QualityFlag.PROVISIONAL
    changelog: str
    target_model_ref: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    raw_files: tuple[RawFileSpec, ...]
    source: SourceSpec
    table: TableSpec
    codebook: CodebookSpec
    variables: tuple[VariableSpec, ...]
    select: tuple[SelectSpec, ...]

    def sha256(self) -> str:
        return sha256_text(canonical_json(self.model_dump(mode="json")))


def load_import_spec(path: str | Path) -> ImportSpec:
    p = Path(path)
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read import spec {p}: {exc}") from exc
    flat: dict[str, Any] = {k: v for k, v in data.items() if k not in ("release", "target")}
    flat.update(data.get("release", {}))
    if "target" in data:
        flat["target_model_ref"] = data["target"].get("model_ref")
    try:
        return ImportSpec.model_validate(flat)
    except ValidationError as exc:
        raise ConfigError(f"invalid import spec {p}:\n{exc}") from exc


# ---------------------------------------------------------------------------------------------- results
@dataclass(frozen=True)
class ImportResult:
    release: DatasetRelease
    report: dict[str, Any]
    dataset_json: str  # exact text that write_release stores
    report_json: str


@dataclass
class _Collector:
    problems: list[str] = field(default_factory=list)

    def add(self, msg: str) -> None:
        self.problems.append(msg)

    def raise_if_any(self, what: str) -> None:
        if self.problems:
            raise DatasetValidationError(f"{what}: {len(self.problems)} problem(s):\n- " + "\n- ".join(self.problems))


# ---------------------------------------------------------------------------------------------- reading
def _read_lines(path: Path) -> list[str]:
    text = path.read_bytes().decode("utf-8")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _parse_line(line: str) -> list[str]:
    return next(csv.reader([line], strict=True))


def _read_table(path: Path, what: str, col: _Collector) -> tuple[list[str], list[tuple[int, str, list[str]]]]:
    """Return (header, [(1-based line number, raw line, cells)])."""
    try:
        lines = _read_lines(path)
    except (OSError, UnicodeDecodeError) as exc:
        col.add(f"{what}: cannot read {path}: {exc}")
        return [], []
    if not lines:
        col.add(f"{what}: file is empty")
        return [], []
    try:
        header = _parse_line(lines[0])
    except csv.Error as exc:
        col.add(f"{what}: header line is not valid CSV: {exc}")
        return [], []
    if len(set(header)) != len(header):
        col.add(f"{what}: duplicate column names in header")
    rows: list[tuple[int, str, list[str]]] = []
    for i, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        try:
            cells = _parse_line(line)
        except csv.Error as exc:
            col.add(f"{what}: line {i} is not a valid single-line CSV row (multi-line quoting unsupported): {exc}")
            continue
        if len(cells) != len(header):
            col.add(f"{what}: line {i} has {len(cells)} cells, header has {len(header)}")
            continue
        rows.append((i, line, cells))
    return header, rows


def parse_decimal(text: str) -> float:
    """Strict decimal text -> finite float64; raises ValueError otherwise (no 'NA', 'nan', 'inf', blanks)."""
    t = text.strip()
    if not _DECIMAL_RE.match(t):
        raise ValueError(f"not a plain decimal number: {text!r}")
    v = float(t)
    if not math.isfinite(v):
        raise ValueError(f"not finite: {text!r}")
    return v


# ---------------------------------------------------------------------------------------------- build
def build_release(
    spec: ImportSpec, raw_root: str | Path, *, model_spec: EnvironmentalModel | None = None
) -> ImportResult:
    """Validate everything and build the release. Raises one ``DatasetValidationError`` listing all problems."""
    col = _Collector()
    root = Path(raw_root)
    if not valid_dataset_id(spec.dataset_id):
        col.add(f"invalid dataset_id {spec.dataset_id!r}")
    if spec.target_model_ref is not None and model_spec is None:
        col.add(f"spec targets model {spec.target_model_ref!r} but no model spec was supplied to validate against")
    given_ref = f"{model_spec.model_id}@{model_spec.version}" if model_spec is not None else None
    if given_ref is not None and spec.target_model_ref is not None and given_ref != spec.target_model_ref:
        col.add(f"spec targets {spec.target_model_ref!r} but was given model {given_ref}")

    # 1. raw files: exist and match their pinned hash
    keys = [r.key for r in spec.raw_files]
    if len(set(keys)) != len(keys):
        col.add("duplicate raw_files key")
    paths: dict[str, Path] = {}
    raw_report: list[dict[str, Any]] = []
    for rf in spec.raw_files:
        try:
            RawFile(source_id=rf.source_id, path=rf.path, sha256=rf.sha256, retrieved_on=rf.retrieved_on, note=rf.note)
        except ValidationError as exc:
            col.add(f"raw file {rf.key!r}: {exc.errors()[0]['msg']}")
            continue
        p = root / rf.path
        if not p.is_file():
            col.add(f"raw file {rf.key!r}: not found at {p}")
            continue
        actual = sha256_file(p)
        if actual != rf.sha256:
            col.add(f"raw file {rf.key!r} ({rf.path}): sha256 {actual} does not match the pinned {rf.sha256}")
            continue
        paths[rf.key] = p
        raw_report.append({"key": rf.key, "path": rf.path, "sha256": actual, "bytes": p.stat().st_size})

    for what, key in (("table", spec.table.file), ("codebook", spec.codebook.file)):
        if key not in {r.key for r in spec.raw_files}:
            col.add(f"{what} refers to unknown raw file key {key!r}")
    col.raise_if_any("import rejected (inputs)")  # nothing below is meaningful without readable, pinned inputs

    # 2. table + codebook
    header, rows = _read_table(paths[spec.table.file], "table", col)
    cb_header, cb_rows = _read_table(paths[spec.codebook.file], "codebook", col)
    col.raise_if_any("import rejected (reading)")
    needed = [
        spec.table.label_column,
        spec.table.period_column,
        *([spec.table.iso3_column] if spec.table.iso3_column else []),
        *(v.column for v in spec.variables),
    ]
    for c in needed:
        if c not in header:
            col.add(f"table has no column {c!r}")
    for c in (spec.codebook.key_column, spec.codebook.unit_column):
        if c not in cb_header:
            col.add(f"codebook has no column {c!r}")
    col.raise_if_any("import rejected (columns)")
    hidx = {c: i for i, c in enumerate(header)}
    cidx = {c: i for i, c in enumerate(cb_header)}

    # 3. units: declared unit text must equal the source's codebook text; unit must parse; model checks
    var_info: dict[str, dict[str, Any]] = {}
    seen_vars: set[str] = set()
    for v in spec.variables:
        if v.variable_id in seen_vars:
            col.add(f"variable {v.variable_id!r} mapped twice")
        seen_vars.add(v.variable_id)
        hits = [r for r in cb_rows if r[2][cidx[spec.codebook.key_column]] == v.column]
        if len(hits) != 1:
            col.add(f"codebook has {len(hits)} rows for column {v.column!r}, expected exactly 1")
            continue
        cb_unit = hits[0][2][cidx[spec.codebook.unit_column]]
        if cb_unit != v.codebook_unit_text:
            col.add(f"variable {v.variable_id!r}: codebook unit text {cb_unit!r} != declared {v.codebook_unit_text!r}")
        try:
            validate_unit_string(v.unit, what=f"variable {v.variable_id!r}")
        except UnitError as exc:
            col.add(str(exc))
        canonical: str | None = None
        bounds = None
        if model_spec is not None:
            inp = next((i for i in model_spec.inputs if i.id == v.variable_id), None)
            if inp is None:
                col.add(f"variable {v.variable_id!r} is not an input of {model_spec.model_id}@{model_spec.version}")
            else:
                canonical, bounds = inp.canonical_unit, inp.physical_bounds
        var_info[v.variable_id] = {
            "codebook_line": hits[0][0],
            "canonical": canonical,
            "bounds": bounds,
            "cb_unit": cb_unit,
        }
    col.raise_if_any("import rejected (variables/units)")

    # 4. rows -> records
    table_rf = next(r for r in spec.raw_files if r.key == spec.table.file)
    spec_hash = spec.sha256()
    source = spec.source
    records: list[DatasetRecord] = []
    rec_report: list[dict[str, Any]] = []
    for sel in spec.select:
        for wc in sel.where:
            if wc not in hidx:
                col.add(f"select {sel.where}: no column {wc!r}")
        if any(wc not in hidx for wc in sel.where):
            continue
        hits = [r for r in rows if all(r[2][hidx[wc]] == wv for wc, wv in sel.where.items())]
        if len(hits) != 1:
            col.add(f"select {sel.where}: matched {len(hits)} rows, expected exactly 1")
            continue
        line_no, raw_line, cells = hits[0]
        label = cells[hidx[spec.table.label_column]]
        period = cells[hidx[spec.table.period_column]]
        if label != sel.where.get(spec.table.label_column, label):
            col.add(f"select {sel.where}: row label {label!r} unexpected")
        if spec.table.iso3_column and sel.source_iso3 is not None:
            iso = cells[hidx[spec.table.iso3_column]]
            if iso != sel.source_iso3:
                col.add(f"select {sel.where}: row iso3 {iso!r} != declared source_iso3 {sel.source_iso3!r}")
        try:
            temporal = TemporalCoverage(start=period, end=period)
        except ValidationError:
            col.add(f"select {sel.where}: period cell {period!r} is not a valid year/date")
            continue
        for v in spec.variables:
            info = var_info[v.variable_id]
            text = cells[hidx[v.column]]
            tag = f"{v.variable_id} / {sel.geography_label} / {period}"
            try:
                value = parse_decimal(text)
            except ValueError as exc:
                col.add(f"{tag}: cell in column {v.column!r} (line {line_no}) rejected: {exc}")
                continue
            if info["canonical"] is not None:
                try:
                    conv = to_canonical(value, v.unit, info["canonical"], what=tag)
                except UnitError as exc:
                    col.add(str(exc))
                    continue
                b = info["bounds"]
                if b is not None:
                    below = b.min is not None and (conv < b.min or (conv == b.min and not b.inclusive_min))
                    above = b.max is not None and (conv > b.max or (conv == b.max and not b.inclusive_max))
                    if below or above:
                        col.add(
                            f"{tag}: {conv} {info['canonical']} violates the model's physical "
                            f"bounds for {v.variable_id!r}"
                        )
                        continue
            geo = sel.geography_code.lower().replace(":", "-")
            rid = f"{v.variable_id}.{geo}.{period}"
            transform = (
                f"none: value is the decimal text {text!r} parsed to float64; unit {v.unit!r} declared by "
                f"EcoBackfire to match the source codebook text {info['cb_unit']!r}; "
                "no scaling, rounding, unit conversion or imputation"
            )
            row_sha = sha256_bytes(raw_line.encode("utf-8"))
            try:
                records.append(
                    DatasetRecord(
                        evidence=EvidenceRecord(
                            record_id=rid,
                            variable_id=v.variable_id,
                            description=v.description,
                            value=value,
                            unit=v.unit,
                            provenance_class=ProvenanceClass.SOURCED,
                            source=SourceRef(
                                source_id=source.source_id,
                                citation=source.citation,
                                url=source.url,
                                locator=(
                                    f"{table_rf.path} line {line_no}: {spec.table.label_column}={label}, "
                                    f"{spec.table.period_column}={period}, column {v.column}"
                                ),
                                retrieved_on=source.retrieved_on,
                            ),
                            scope=Scope(
                                geography=sel.geography_label, period=period, system_description=v.system_description
                            ),
                            note=v.note,
                        ),
                        temporal=temporal,
                        geography_codes=(sel.geography_code,),
                        metadata={
                            "raw_column": v.column,
                            "raw_value_text": text,
                            "raw_file": table_rf.path,
                            "raw_line": str(line_no),
                            "raw_row_sha256": row_sha,
                            "transformations": transform,
                            "import_spec_sha256": spec_hash,
                        },
                    )
                )
            except ValidationError as exc:
                col.add(f"{tag}: record rejected: {exc.errors()[0]['msg']}")
                continue
            rec_report.append(
                {
                    "record_id": rid,
                    "variable_id": v.variable_id,
                    "raw_value_text": text,
                    "value": value,
                    "unit": v.unit,
                    "raw_file": table_rf.path,
                    "raw_line": line_no,
                    "raw_row_sha256": row_sha,
                    "transformations": [transform],
                }
            )
    col.raise_if_any("import rejected (records)")

    meta = dict(spec.metadata)
    meta["import_spec_sha256"] = spec_hash
    meta["importer_version"] = IMPORTER_VERSION
    try:
        release = DatasetRelease(
            dataset_id=spec.dataset_id,
            release_version=spec.release_version,
            title=spec.title,
            description=spec.description,
            provider=spec.provider,
            methodology=spec.methodology,
            license=spec.license,
            last_updated=spec.last_updated,
            quality=spec.quality,
            changelog=spec.changelog,
            raw_files=tuple(
                RawFile(source_id=r.source_id, path=r.path, sha256=r.sha256, retrieved_on=r.retrieved_on, note=r.note)
                for r in spec.raw_files
            ),
            metadata=meta,
            records=tuple(records),
        )
    except ValidationError as exc:
        raise DatasetValidationError(f"import rejected (release): {exc}") from exc

    dataset_json = json.dumps(release.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    report = {
        "importer_version": IMPORTER_VERSION,
        "import_spec_sha256": spec_hash,
        "dataset_id": release.dataset_id,
        "release_version": release.release_version,
        "target_model_ref": spec.target_model_ref,
        "raw_files": raw_report,
        "n_records": len(records),
        "records": rec_report,
        "release_content_sha256": sha256_text(canonical_json(release.model_dump(mode="json"))),
        "dataset_json_sha256": sha256_text(dataset_json),
        "value_transformations": "none (see per-record 'transformations')",
    }
    report_json = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return ImportResult(release=release, report=report, dataset_json=dataset_json, report_json=report_json)


# ---------------------------------------------------------------------------------------------- write / verify
def write_release(result: ImportResult, registry_root: str | Path) -> tuple[Path, bool]:
    """Store the release under ``<root>/<id>/<version>/``. Returns (dataset.json path, newly_written).

    Append-only: an existing release with identical bytes is left alone (idempotent); different bytes raise.
    """
    rel = result.release
    d = Path(registry_root) / rel.dataset_id / rel.release_version
    target = d / DATASET_FILENAME
    if target.exists():
        if target.read_text(encoding="utf-8") == result.dataset_json:
            return target, False
        raise DatasetValidationError(
            f"{rel.dataset_id}@{rel.release_version} already exists with different content; releases are "
            "append-only, publish the change as a new release_version"
        )
    d.mkdir(parents=True, exist_ok=True)
    target.write_text(result.dataset_json, encoding="utf-8")
    (d / REPORT_FILENAME).write_text(result.report_json, encoding="utf-8")
    return target, True


def verify_raw_files(release: DatasetRelease, raw_root: str | Path) -> list[str]:
    """Recompute the hash of every raw file a release pins. Returns problems (empty = all verified)."""
    problems: list[str] = []
    for rf in release.raw_files:
        p = Path(raw_root) / rf.path
        if not p.is_file():
            problems.append(f"{release.dataset_id}@{release.release_version}: raw file missing: {p}")
        elif sha256_file(p) != rf.sha256:
            problems.append(
                f"{release.dataset_id}@{release.release_version}: raw file {rf.path} hash differs from the pinned value"
            )
    return problems
