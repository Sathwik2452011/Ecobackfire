# Datasets and the local registry (Evidence & Dataset Intelligence, Step 1)

Status: **Step 1 implemented** (schema + registry); **real-data import path and first real release added** (see `docs/dataset-import.md`). Parameter resolution, conflict handling, uncertainty
propagation, discovery integration, provenance/CLI come in Steps 2-6 (`PROJECT_STATUS.md`).

## Design in one paragraph
A dataset *release* is one JSON file. Each value in it is an existing `EvidenceRecord` (value, unit, uncertainty,
`ProvenanceClass`, `SourceRef`, `Scope`, review) wrapped in a `DatasetRecord` that adds only dataset context.
`DatasetRegistry` scans a directory, validates every release, and answers catalogue questions. It never picks a
value for a model; that is the resolver's job (Step 2).

## Layout
`<root>/<dataset_id>/<release_version>/dataset.json`, default root `data/releases/`.
`dataset_id`: `[a-z0-9][a-z0-9._-]{0,63}`. `release_version`: strict `MAJOR.MINOR.PATCH`. The id and version in
the file must match the directory (mismatch = error). Releases are append-only.

## `dataset.json`
| Field | Required | Notes |
|---|---|---|
| `schema_version` | default `1.0.0` | other values rejected |
| `dataset_id`, `release_version` | yes | |
| `title`, `description`, `provider`, `changelog` | yes | non-empty. `provider` = who published the underlying data |
| `last_updated` | yes | ISO date `YYYY-MM-DD` |
| `methodology` | no | release-level measurement/estimation context; records may override |
| `license` | no | `{identifier?, text?, url?, redistribution_allowed?}` (at least one of the first three; `null` = unknown, never "allowed") |
| `quality` | default `provisional` | `provisional | accepted | deprecated | withdrawn` |
| `parent_release` | no | must be an earlier version |
| `raw_files` | no | `[{source_id, path (relative, no ..), sha256, retrieved_on?, note?}]` (hash verification: Step 6) |
| `missing_variables` | no | `[{variable_id, reason}]` explicit documented gaps; a variable cannot be both missing and present |
| `metadata` | no | string-to-string map |
| `records` | yes* | *a release needs at least one record or one missing variable |

### `records[]` (a `DatasetRecord`)
| Field | Notes |
|---|---|
| `evidence` | an `EvidenceRecord` exactly as in `ecobackfire.evidence` (`record_id`, `variable_id`, `description`, `value`, `unit`, `uncertainty`, `provenance_class`, `source`, `scope`, `review`, `note`). Its integrity rules apply unchanged (cited classes need a source; illustrative needs the marker and no source) |
| `methodology` | optional override of the release text |
| `temporal` | optional `{start, end}`, each `YYYY`, `YYYY-MM` or `YYYY-MM-DD`; a partial date spans its whole period |
| `geography_codes` | optional: `GLOBAL`, `M49:NNN`, ISO 3166-1 alpha-2 (`IN`), ISO 3166-2 (`IN-AP`) |
| `quality` | optional override of the release flag |
| `derived_from`, `derivation` | parent record ids (same release) and the documented procedure. `estimated` needs both, `simulated` needs `derivation`; `measured`/`sourced` must not have them. A derived record cannot be stronger than any parent (no promotion); cycles and self-derivation are rejected |
| `metadata` | string-to-string map |

## Evidence status (derived, never typed in)
| Status | From |
|---|---|
| `measured` | `provenance_class = measured` |
| `verified_source` | `sourced` and a named human review is attached |
| `sourced_unreviewed` | `sourced`, no review (added beyond the brief's minimum so an unchecked citation is never shown as verified) |
| `derived` | `estimated` or `simulated` (the record keeps its own class) |
| `assumption` | `user_supplied` |
| `illustrative` | `illustrative` |
| `missing`, `conflicting` | **resolution outcomes only**; cannot be stored on a record |

`weakest_status` orders by origin only. It is not a confidence number, and "evidence-backed" is still gated by
`ecobackfire.evidence.is_evidence_backed` (reviewed + geography + period + characterized uncertainty).

## Registry API (programmatic)
`DatasetRegistry.from_directory(root, strict=True)`, `.dataset_ids()`, `.versions(id)`, `.latest_version(id)`,
`.get(id, version)` (explicit version; no implicit "latest"), `.entries(variable_id=, dataset_id=, version=,
usable_only=)`, `.entry(evidence_id)`, `.variables(...)`, `.sources(...)`, `.coverage(...)`, `.describe(...)`,
`.index()`, `.sha256()`, `.parameter_availability(model_spec)`, `.evidence_store()`.
Evidence ids are `<dataset_id>@<version>:<record_id>`. `evidence_store()` exposes records under those ids so the
existing `EvidenceStore.check_binding` works on dataset-backed bindings.

`parameter_availability` lists, per model parameter, registry entries with an eligibility verdict (unit
convertible to the canonical unit; quality not deprecated/withdrawn), parameters with no entry, and documented
missing variables. Eligible means "may be offered", not "correct for this geography/period".

## Hashes
`file_sha256` = bytes of `dataset.json` as read; `content_sha256` = canonical JSON of the validated release
(ignores formatting); `registry.sha256()` = over (id, version, file hash) of all releases.

## Known limitations (Step 1)
- Geography codes are syntax-checked only (no ISO list ships); M49 membership is not resolved.
- Temporal plausibility (e.g. implausible years) and "stale dataset" detection belong to Step 2 validation.
- Release immutability relies on git plus recorded hashes; the registry cannot stop an edit, only expose it. The importer refuses to overwrite a different release, and `datasets validate` re-hashes pinned raw files.
- `geography.covers`: `GLOBAL` covers every requested geography. A resolver must therefore prefer the most specific matching record and report any fallback to a broader one; a world average must never silently stand in for a country that has its own record (Step 2).
- Source existence is not verified; a citation is a claim, as in the evidence layer.
- Deviation from docs/data-contracts.md section 4: manifest and records are one file here.
