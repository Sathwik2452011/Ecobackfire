# Evidence-backed parameter resolution (Step 2)

Chain: `model parameter -> dataset variable -> dataset version -> record (source/evidence) -> value + unit + scope + provenance`.

Code: `contracts/resolution.py` (layer 0 models), `datasets/resolution.py` (resolver),
`experiments/evidence.py` (`materialize_bindings`, `materialize_config`, `alternative_configs`). Wired into
`run_experiment`, `run_discovery`, `verify`, `api.execute/reverify/reproduce`, the manifest, the Markdown report and the CLI
(`--registry`, `ecobackfire resolve`).

## Asking for evidence

An experiment TOML or discovery spec gets an optional table per input; that input then has NO `[bindings.X]`:

```toml
[evidence_requests.f]
dataset_id = "owid-electricity-carbon-intensity"
version    = "1.0.0"      # exact; 'latest' is rejected
geography  = "IN"         # GLOBAL | M49:NNN | ISO alpha-2 | ISO 3166-2
period     = "2024"       # YYYY[-MM[-DD]] or START/END
# opt-ins, all default off: geography_fallback, temporal_match = "overlaps", include_deprecated,
# minimum_status, select = "<dataset>@<version>:<record>"
```

The field is omitted from serialization when empty, so configurations and results that use no evidence serialize and
hash exactly as before (E2 stays `ec25be88`).

## Rules (every excluded record is listed with all of its reasons)

| Rule | Behaviour |
|---|---|
| unit | converted with pint; incompatible or unknown unit excludes the record (`unit_incompatible`); original value and unit are kept and the conversion is recorded |
| bounds | a canonical value violating the model's physical bounds excludes the record |
| quality / status | withdrawn never resolves; deprecated only with `include_deprecated`; `minimum_status` filters by origin rank |
| geography | exact match only; a broader or GLOBAL record needs `geography_fallback = true`; the most specific tier wins and the fallback is a warning; records without `geography_codes` are never matched |
| period | the record's period must contain the request (`overlaps` is opt-in); records without structured coverage never match |
| several applicable | all uncertainty intervals overlap (a point when none is stated) -> resolved, strongest origin then smallest id, the others preserved; any disjoint pair -> **conflicting**, nothing is chosen |
| conflict | the caller picks with `select` (recorded as `overridden_by_caller`) or evaluates each source with `alternative_configs`; running on an unresolved request raises `EvidenceConflictError` / `MissingEvidenceError` |
| missing | explicit `missing` state; no default and no illustrative fallback |

## What a run records

`ExperimentResult.evidence_resolution` and `DiscoveryResult.evidence_resolution`: registry sha256, release file and
content hashes, and for EVERY bound input its basis (`evidence | assumed | illustrative`). For evidence inputs: record id,
status, source, scope, original value and unit, conversion, uncertainty/range, all applicable and excluded records, the
conflict report, the selection rule, warnings and the decision log. Each discovery candidate and region carries
`input_evidence` (searched vs held at base). The manifest gets `dataset_release_ref`, `evidence_resolution_sha256` and
`evidence_registry_sha256`. `config.json` stores the EFFECTIVE configuration (requests plus resolved bindings);
`reproduce` and `verify` re-resolve it and fail visibly if registry content changed.

## Trust boundary

`prepare_inputs` still refuses `measured` / `sourced` bindings unless the caller passes `verified_evidence`
(input -> evidence id) produced by the resolver against a registry. A file that merely claims "sourced" is refused.

## Limits

- Conflict detection uses interval overlap (relative tolerance 1e-9). Interval bounds are converted bound by bound, which is
  exact for multiplicative units only.
- M49 region membership is not resolved; GLOBAL is never used for a country without opt-in.
- Falsification and robustness remain pure numerics not yet wired to `Candidate`; `discovery/provenance.py` provides the join
  keys (`evidence_ids_for`, `provenance_digest`).
- A resolved input is the value of one dataset record under the stated request. It is not a validated real-world claim.
