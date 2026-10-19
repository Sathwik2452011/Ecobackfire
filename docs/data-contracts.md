# Data Contracts

Status: **Blueprint.** Signatures below are design specifications, not implemented code. They will be implemented as `pydantic` v2 models in `src/ecobackfire/contracts/` in the phases listed in `implementation-roadmap.md`. Phase 1 implements only a minimal subset (marked **P1**).

## 0. Conventions that apply to every schema

- Every persisted object has `schema_version: str` (SemVer `MAJOR.MINOR.PATCH`) and, where it is a named artifact, a stable `id`.
- **Versioning strategy:**
  - PATCH: documentation/description changes, no field changes.
  - MINOR: new *optional* fields or new enum members that old readers can ignore safely.
  - MAJOR: removed/renamed/retyped fields, tightened semantics. Requires a migration function `migrate_<Schema>_vX_to_vY` and a test that loads an archived v(X) fixture.
  - Archived fixtures of each released schema version live in `tests/software/fixtures/schemas/`.
- JSON Schema is exported per model to `docs/schemas/<Schema>-<version>.json`; a test fails if the exported schema differs from the checked-in file without a version bump.
- Models use `extra="forbid"` (unknown fields are errors) and `frozen=True` (immutable once built).
- Numeric values are stored with their unit string (pint-parseable) and, where applicable, an `Uncertainty`. Bare floats representing physical quantities are not allowed in persisted artifacts.
- Content hashes: `sha256` of canonical JSON (UTF-8, sorted keys, no whitespace, floats serialized with `repr`, excluding any field named `created_at`, `retrieved_at_utc` if the hash is declared "content-only").
- IDs: lowercase `[a-z0-9._-]`, max 128 chars. Versioned references use `id@version`.
- Timestamps are timezone-aware UTC ISO-8601 and are never inputs to a calculation.

## 1. Shared building blocks

### `Provenance` (enum `ProvenanceClass`)
`measured` | `sourced` | `estimated` | `simulated` | `user_supplied` | `illustrative`
Ordering for "weakest-link" labeling, strongest to weakest: `measured > sourced > estimated > simulated > user_supplied > illustrative`. [PROVISIONAL: ordering is a policy choice and documented in `scientific-method.md`; `user_supplied` may legitimately outrank `sourced` if the user is the measurement owner. The ordering is a configuration table, not hard-coded logic.]

### `Uncertainty` (tagged union on `kind`) **P1 (point, interval, uniform, triangular, normal, lognormal)**
| kind | fields | validation |
|---|---|---|
| `point` | none | value is exact by declaration (e.g., a definition or a scenario choice), **not** "unknown uncertainty" |
| `unknown` | none | uncertainty not characterized; propagation must refuse or require a user-provided assumption |
| `interval` | `low`, `high` | `low <= high`; same unit as value; interpreted as bounds with no distribution implied |
| `uniform` | `low`, `high` | `low < high` |
| `triangular` | `low`, `mode`, `high` | `low <= mode <= high`, `low < high` |
| `normal` | `sd` or `ci95_low/ci95_high` | `sd > 0`; truncation bounds optional |
| `lognormal` | `geometric_mean`, `geometric_sd` (>= 1) or equivalent | positive support |
| `ensemble` | `samples: list[float]` or `samples_uri` + hash | non-empty, finite |

`unknown` and `point` are deliberately distinct so absence of uncertainty information is never read as certainty.

### `ScopeDescriptor`
- `geography`: list of ISO 3166-1/-2 codes or named regions; `"global"` allowed; `"unspecified"` allowed but flagged.
- `time_period`: `start`, `end` (ISO dates) and/or `reference_year`.
- `technology_description`: free text plus optional controlled vocabulary tags.
- `system_boundary_ref`: optional reference to a `SystemBoundary`.

### `SystemBoundary`
`id`, `description`, `included_stages: list[str]`, `excluded_stages: list[str]`, `cutoff_rules: str`, `allocation_method: str | None`. Validation: no stage appears in both lists.

### `FunctionalUnit`
`id`, `description`, `quantity_unit` (pint string), `reference_flow_description`. Example: "1 hour of lighting at 500 lux over 10 m²".

### `ValidationIssue`
`severity (error|warning|info)`, `tier (schema|physical|applicability)`, `code`, `message`, `path`.

## 2. `EnvironmentalVariable` **P1**

Describes a named quantity that models consume or produce. A *definition*, not a value.

| Field | Req | Notes / validation |
|---|---|---|
| `id` | yes | unique within a model or a shared variable catalog |
| `name` | yes | human-readable |
| `description` | yes | what it means, including inclusions/exclusions |
| `dimension` | yes | pint dimensionality string, e.g. `[energy]`, `[mass]`, `dimensionless`; unit strings are validated against it |
| `canonical_unit` | yes | pint-parseable and compatible with `dimension` |
| `kind` | yes | `parameter | state | output | metric` |
| `physical_bounds` | optional | `{min, max, inclusive_min, inclusive_max}` in canonical unit; violation is an error |
| `plausible_range` | optional | range used for exploration and applicability warnings; must lie within or equal `physical_bounds` (outside is a validation error) |
| `quantity_kind` | optional | e.g. `intensity`, `fraction`, `elasticity`, `stock`, `flow`; used to forbid nonsensical operations (e.g. adding a stock to a rate) |
| `functional_unit_ref` | optional | required when `quantity_kind` is `intensity` |
| `tags` | optional | domain vocabulary |

Relationships: referenced by `EnvironmentalModel.inputs/outputs`, `EvidenceRecord.variable_id`.

## 3. `EvidenceRecord` (P1 only as the internal `Binding` shortcut; full record in Phase 6)

One sourced value (or distribution) for one variable in one scope.

| Field | Req | Notes / validation |
|---|---|---|
| `schema_version`, `id` | yes | |
| `variable_id` | yes | refers to an `EnvironmentalVariable` |
| `value` | yes | numeric (float) or `MissingValue` marker (see below) |
| `unit` | yes | original unit string as reported |
| `uncertainty` | yes | `Uncertainty`; use `unknown` if not reported |
| `normalized_value`, `normalized_unit` | derived | must equal conversion of `value` by `conversion_factor`; recomputed and checked on load |
| `conversion_factor` / `conversion_note` | when normalized | stored, never discarded |
| `provenance_class` | yes | `ProvenanceClass` |
| `kind` | yes | `observation | reported_estimate | derived | simulated | user_supplied | illustrative` (must be consistent with `provenance_class`; validator enforces allowed pairs) |
| `source` | yes unless `illustrative` | `SourceRef` (below) |
| `source_location` | when `source` present | page/table/figure/row/cell pointer |
| `quote` | optional | verbatim excerpt only if `license.allows_quotation` |
| `scope` | yes | `ScopeDescriptor` |
| `system_boundary_ref`, `functional_unit_ref` | when applicable | mismatch with a model's declared boundary is an applicability warning |
| `parent_record_ids` | required if `kind == derived` | each must resolve; `derivation_code_version` required |
| `extraction_method` | yes | `manual | script | ai_assisted` |
| `human_reviewed` | yes | `bool`; default `false` when `ai_assisted` |
| `notes` | optional | |
| `created_at` | yes | metadata, excluded from content hash |

`MissingValue`: `{ "missing": true, "reason": str }`. It cannot be coerced to a number; any attempt in model evaluation raises `MissingEvidenceError`.

`SourceRef`: `source_id`, `citation` (free text as given by the curator), `doi` / `url` (optional), `retrieved_at_utc`, `license` (SPDX id or text), `raw_file_path`, `raw_file_sha256`.
**Rule:** `illustrative` records may omit `source` but must carry the literal note `"ILLUSTRATIVE — not evidence"`, and any report that uses one displays a banner.

Invariants: `ai_assisted` records are never `human_reviewed` by default; `illustrative` records can never be promoted to a stronger class by normalization or derivation (a derived record inherits the weakest parent class).

## 4. `DatasetManifest` (Phase 6)

| Field | Req | Notes |
|---|---|---|
| `schema_version`, `dataset_id`, `release_version` | yes | `release_version` is SemVer; releases immutable |
| `created_at`, `build_code_version` | yes | |
| `record_ids` | yes | ordered; each resolves in the release's records file |
| `records_file`, `records_sha256` | yes | content-addressed |
| `raw_sources` | yes | list of `SourceRef` with checksums |
| `licenses_summary` | yes | union of licenses; flags non-redistributable items |
| `coverage` | yes | geographic/temporal summary computed from records |
| `known_conflicts` | yes (may be empty) | list of `ConflictRecord` (`variable_id`, `record_ids`, `description`, `status: unresolved|resolved_by|accepted_as_scenarios`) |
| `missing_variables` | yes (may be empty) | explicit list of required-but-absent variables |
| `parent_release` | optional | previous version |
| `changelog` | yes | human-readable |

Validation: recomputed `records_sha256` must match; unresolved conflicts do not block a release but are surfaced in every report that uses an affected variable.

## 5. `ModelAssumptions` **P1**

| Field | Req | Notes |
|---|---|---|
| `assumptions` | yes, non-empty | list of `{id, statement, rationale, testable: bool, test_ref?}` |
| `exclusions` | yes (may be empty with explicit `"none declared"`) | what the model leaves out (e.g., indirect rebound, grid dynamics) |
| `functional_unit` | yes | `FunctionalUnit` |
| `system_boundary` | yes | `SystemBoundary` |
| `applicability` | yes | per-variable valid ranges and qualitative conditions; used for tier-3 warnings |
| `known_limitations` | yes | list of text |
| `alternative_formulations` | optional | ids of other models/forms that test the same question (feeds red-team "competing assumptions") |

## 6. `EnvironmentalModel` **P1**

| Field | Req | Notes / validation |
|---|---|---|
| `schema_version`, `model_id`, `version` | yes | version SemVer; any change to equations or defaults bumps version |
| `title`, `description` | yes | |
| `domain` | yes | e.g., `energy.efficiency` |
| `inputs` | yes | list of `EnvironmentalVariable`; ids unique |
| `outputs` | yes | list of `EnvironmentalVariable` |
| `assumptions` | yes | `ModelAssumptions` |
| `constraints` | yes (may be empty) | machine-checkable expressions over inputs (e.g., `0 <= eta < 1`), each with `severity` |
| `decision_metrics` | yes (>= 1 for discovery) | `{id, output_id, comparator, threshold, direction_text}` e.g., "intervention beneficial iff `delta_emissions < 0`" |
| `equations` | yes | documentation-grade list `{id, text, latex?}`; the code implements them; a test checks that each id is cited in a trace |
| `references` | optional | `SourceRef`s for the model form (not for parameters) |
| `validation_cases` | yes before status `reviewed` | ids of cases in `benchmarks/` |
| `status` | yes | `draft | reviewed | deprecated` |
| `implementation` | yes | `entry_point` string resolved by the registry; not executed during schema validation |

Rules: a model without `functional_unit` and `system_boundary` fails validation. Changing `decision_metrics` bumps MINOR at least.

## 7. `ExperimentConfiguration` **P1**

| Field | Req | Notes / validation |
|---|---|---|
| `schema_version`, `experiment_id` | yes | |
| `title`, `research_question` | yes | free text, included in report |
| `model_ref` | yes | `model_id@version`; exact version required (no "latest") |
| `dataset_release_ref` | optional in P1 | `dataset_id@release_version`; required once Phase 6 lands for any non-illustrative binding |
| `kind` | yes | `compare | scenario | oat_sweep | breakeven | grid_2d | global_sensitivity | monte_carlo` |
| `baseline` | for compare-like kinds | `Bindings` (below) |
| `intervention` | for compare-like kinds | `Bindings` or `overrides` of baseline |
| `bindings` | yes | map `variable_id -> Binding` |
| `search` | for sweep/breakeven | `{variable_id, range{low,high,unit}, scale: linear|log, grid_points >= 3, tolerance}` |
| `decision_metric_ref` | yes for breakeven/discovery | id in the model |
| `sampling` | for stochastic kinds | `{method, n, seed}`; `seed` required (no default) |
| `boundary_variants` | optional | list of alternate `SystemBoundary`/model options to compare |
| `verification_profile` | optional | named set of checks; default `standard` |
| `report` | optional | output options |

`Binding`: `{variable_id, value, unit, uncertainty, provenance_class, evidence_record_id?, note?}`.
Validation: every model input must be bound or the model must define an explicit default marked `provenance_class=illustrative`; an unbound input without default raises `MissingEvidenceError` at `load_config`, not at runtime. Units must be dimensionally compatible with the variable. `evidence_record_id` is required when `provenance_class in {measured, sourced}`.

## 8. `ExperimentResult` **P1**

| Field | Req | Notes |
|---|---|---|
| `schema_version`, `result_id`, `experiment_id` | yes | |
| `config_sha256` | yes | hash of canonical `ExperimentConfiguration` |
| `model_ref`, `dataset_release_ref` | yes | |
| `status` | yes | `completed | completed_with_warnings | failed` |
| `outputs` | yes | for compare: per-scenario output quantities with units and uncertainty; for sweeps: a table file ref + hash |
| `decision` | when metric defined | `{metric_id, value, beneficial: bool|null, margin}` |
| `breakeven` | optional | `{variable_id, value, unit, bracket, method, tolerance, iterations, converged}` |
| `reversal_regions` | optional | list of intervals/regions where the decision flips |
| `sensitivity` | optional | rankings with method + settings |
| `uncertainty_summary` | optional | percentiles, `P(reversal)`, Monte Carlo standard error |
| `input_provenance_summary` | yes | for each input: provenance class and whether it was influential; **weakest influential class** |
| `system_boundary`, `functional_unit` | yes | copied from the model so a result is self-describing |
| `warnings` | yes (may be empty) | all tier-3 and numerical warnings |
| `trace_ref` | optional | |
| `runtime_metadata` | yes | excluded from content hash |

Invariant: result is invalid if `breakeven` is present without a recorded bracket with opposite decision signs at its ends.

## 9. `Hypothesis`

| Field | Req | Notes |
|---|---|---|
| `schema_version`, `hypothesis_id` | yes | |
| `statement` | yes | precise, falsifiable, includes the condition and the predicted direction/threshold |
| `origin` | yes | `user | strategy:<name>@<ver> | ai:<provider>:<model>` |
| `model_ref` | yes unless purely evidence-level | |
| `variables_involved` | yes | |
| `prediction` | yes | machine-checkable form: `{decision_metric_ref, condition, expected_outcome}` |
| `falsification_criteria` | yes | what result would refute it (must be non-empty) |
| `supporting_evidence_ids` | optional | |
| `scores` | optional | `{evidence_quality, testability, potential_impact, uncertainty, rank_score, weights_ref}` |
| `status` | yes | `unverified | testing | refuted | inconclusive | promoted` |
| `created_at` | yes | metadata |

A `Hypothesis` without `falsification_criteria` is rejected. `origin` is immutable and carried into reports.

## 10. `CandidateFinding`

Created when an experiment appears to confirm a hypothesis (e.g., a reversal or break-even exists).

| Field | Req | Notes |
|---|---|---|
| `schema_version`, `finding_id` | yes | |
| `hypothesis_id` | yes | |
| `result_ids` | yes | supporting `ExperimentResult`s |
| `claim` | yes | restated with conditions and units; includes system boundary and functional unit text |
| `conditions` | yes | the parameter region in which the claim holds |
| `evidence_summary` | yes | provenance classes, conflicts touching influential inputs |
| `classification` | yes | enum in `scientific-method.md`: `unverified_hypothesis | candidate | verified_internal | conditional | refuted | inconclusive`. Only `verification` may set values above `candidate`; the constructor refuses otherwise. |
| `verification_report_id` | required for any classification above `candidate` | |
| `limitations` | yes, non-empty | |
| `prior_art_status` | yes | `not_reviewed | reviewed_no_match | reviewed_known`; novelty language is forbidden unless `reviewed_no_match` with a recorded review reference |

Note: the engine can never set `established` or `independently_reproduced`; those are human-curated states (see `scientific-method.md`).

## 11. `VerificationReport` **P1 (minimal)**

| Field | Req | Notes |
|---|---|---|
| `schema_version`, `report_id`, `finding_id` or `result_id` | yes | |
| `profile` | yes | named check set and its version |
| `checks` | yes | list of `{check_id, family, status: passed|failed|inconclusive|skipped|not_applicable, details, evidence_refs, tolerance_used, reason_if_skipped}` |
| `unresolved_uncertainties` | yes (may be empty) | |
| `failed_attempts_to_falsify` | yes | falsification tests that *did not* refute the finding, with parameters used |
| `successful_falsifications` | yes (may be empty) | tests that refuted or narrowed the finding |
| `recommended_label` | yes | output of the pure labeling function |
| `label_justification` | yes | rules fired, in order |
| `code_version`, `check_suite_version` | yes | |

Invariants: `recommended_label` must equal `label(checks)` recomputed on load. Any `failed` check in a `required` family caps the label at `refuted` or `inconclusive`. `skipped` required checks cap the label below `verified_internal`.

## 12. `ReproducibilityManifest` **P1**

| Field | Req | Notes |
|---|---|---|
| `schema_version`, `manifest_id`, `run_id` | yes | |
| `created_at` | yes | metadata |
| `code` | yes | `{package_version, git_commit | null, git_dirty: bool}` (dirty tree is flagged prominently) |
| `python`, `platform` | yes | |
| `dependencies` | yes | pinned versions of all installed direct dependencies |
| `config_sha256`, `config_file` | yes | |
| `model_ref`, `dataset_release_ref` | yes | |
| `dataset_manifest_sha256` | when dataset used | |
| `seed`, `rng_algorithm` | when stochastic | |
| `artifact_hashes` | yes | sha256 of result, verification, trace, tables |
| `reproduce_command` | yes | exact CLI invocation |
| `expected_tolerance` | yes | `{rtol, atol}` for float comparison on other platforms |

Reproduction semantic: `ecobackfire reproduce manifest.json` reruns in a scratch dir, compares artifact content hashes (exact for JSON structure; numerics within `expected_tolerance`), and reports each mismatch.

## 13. Relationship map

```
EnvironmentalVariable ◄── EnvironmentalModel ──► ModelAssumptions
        ▲                        ▲  ▲
        │                        │  └──────────── ExperimentConfiguration ──► (dataset release)
EvidenceRecord ◄── DatasetManifest                       │
        ▲                                                 ▼
        └──────── bindings ─────────────────────── ExperimentResult
                                                          │
                       Hypothesis ──► CandidateFinding ◄──┘
                                           │
                                           ▼
                                  VerificationReport
                                           │
                                           ▼
                              ReproducibilityManifest (covers config, result, verification)
```

## 14. Cross-schema validation rules (tested in `tests/software`)

1. Unit/dimension compatibility is checked at every binding; incompatible units never reach a model.
2. A `Binding` with class `measured`/`sourced` must point to a resolvable `EvidenceRecord`.
3. No persisted result may reference a dataset release that is not hash-verifiable.
4. A `CandidateFinding` classification above `candidate` without a `VerificationReport` is invalid.
5. A result produced with any `illustrative` influential input must display `illustrative` as its weakest class, and the report must carry the banner.
6. Changing a model's equations without a version bump is detected by an equation-hash stored in the model spec and recomputed by a test. [PROVISIONAL: equation hash covers the documentation text, not source code; the benchmark suite is the stronger guard.]
