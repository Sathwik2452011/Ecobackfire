# EcoBackfire Architecture

Status: **Blueprint (Phase 0) plus the Phase 1 vertical slice** (`docs/phase-2-vertical-slice.md`). Implemented so far: `contracts`, `units`, `core`, `models/efficiency_rebound`, `experiments` (compare, breakeven, oat_sweep), `verification`, `reporting`, `cli`, `api`. Not yet implemented: `evidence`, `datasets`, `discovery`, `ai`. Implementation deviations are listed in section 1 of the vertical-slice document; the layer rules below are enforced by `tests/software/test_import_boundaries.py`.
Repository inspection result: the repository was empty (no code, tests, docs, or configuration). Nothing is reused, isolated, or replaced; everything below is a from-scratch design. If code appears before Phase 1 starts, re-run the inspection and add a "Reuse / Isolate / Replace" table here before writing code.

Labels used throughout: **[DECISION]** firm choice, **[PROVISIONAL]** reasonable default that Phase 1 may revise with evidence, **[OPEN]** unresolved (also listed in `risks-and-open-questions.md`).

---

## 1. Design goals in one paragraph

EcoBackfire is a deterministic, offline-capable Python library and CLI that (a) runs declared environmental models, (b) searches their parameter space for break-even points and decision reversals, (c) tries to falsify every such finding, and (d) emits machine-readable and human-readable reports that can be reproduced from a manifest. AI is an optional adapter that may *propose* hypotheses and *extract* evidence; it can never produce a number that appears in a result.

## 2. Technical foundation

| Concern | Choice | Justification | Status |
|---|---|---|---|
| Language | Python >= 3.11 | Scientific ecosystem, solo-developer productivity, `tomllib` in stdlib | DECISION |
| Schemas / validation | `pydantic` v2 | Typed models, JSON Schema export (needed for versioned report schemas), strict validation | DECISION |
| Units | `pint` | Mature dimensional analysis; rejects incompatible quantities; avoids hand-rolled unit bugs | DECISION |
| Numerics | `numpy`, `scipy` | Root finding (`brentq`), quasi-random sampling (`scipy.stats.qmc`), distributions | DECISION |
| Sensitivity analysis | `scipy.stats.qmc` + own thin Morris/Sobol wrappers; `SALib` only as optional extra if wrappers prove error-prone | Fewer dependencies in the core; SALib is well tested but adds weight | PROVISIONAL |
| CLI | `argparse` (stdlib) | Zero dependency; the CLI surface is small | PROVISIONAL (switch to `typer` if subcommands grow) |
| Config files | TOML (read via stdlib `tomllib`) for human-authored experiment configs; JSON for all machine artifacts | No YAML dependency; TOML is unambiguous | DECISION |
| Hashing | `hashlib.sha256` over canonical JSON (sorted keys, normalized floats) | Reproducibility manifests, dataset releases | DECISION |
| Storage v1 | Plain files in the repo/`runs/` dir (JSON, CSV, Markdown) | No database server; diffable; git-friendly | DECISION |
| Storage later | stdlib `sqlite3` index over the files, only when a query need is demonstrated | Avoids premature infrastructure | PROVISIONAL |
| Tests | `pytest`, `hypothesis` (property tests for units and invariants) | Property tests are a natural fit for invariants | DECISION |
| Quality | `ruff` (lint+format), `mypy --strict` on `contracts/` and `core/` | Cheap, catches schema drift | DECISION |
| Packaging | `pyproject.toml`, `src/` layout, `hatchling` backend, `uv` or `pip` for installs | Standard; no lock-in | PROVISIONAL |

**Explicitly not used:** vector databases, agent frameworks, microservices, ML libraries, web frameworks, ORMs, message queues, containers as a requirement. Any of these requires a written requirement in `risks-and-open-questions.md` before adoption.

**Core dependency budget:** `pydantic`, `pint`, `numpy`, `scipy`. Everything else is a dev dependency or an optional extra (`ai`, `sensitivity`, `docs`).

## 3. Repository structure

```
ecobackfire/
├── README.md
├── pyproject.toml
├── docs/                         # this blueprint, then living docs
├── src/ecobackfire/
│   ├── contracts/                # Layer 0: pure data schemas (pydantic). No logic beyond validation.
│   ├── units/                    # Layer 0: pint registry, EcoBackfire-specific units, quantity helpers
│   ├── core/                     # Layer 1: model protocol, calculation trace, validation, uncertainty primitives
│   ├── models/                   # Layer 2: environmental model definitions, one subpackage per domain
│   │   └── efficiency_rebound/   #   first model (see first-experiment.md)
│   ├── evidence/                 # Layer 2: ingestion adapters, unit normalization, conflict detection
│   ├── datasets/                 # Layer 2: manifest building, release hashing, loaders
│   ├── experiments/              # Layer 3: runner, scenarios, sweeps, sensitivity, break-even, UQ
│   ├── discovery/                # Layer 4: hypothesis generators, search strategies, ranking, candidate queue
│   ├── verification/             # Layer 4: red-team checks, invariants, reference comparisons, labeling gate
│   ├── reporting/                # Layer 5: JSON + Markdown report rendering, ReproducibilityManifest assembly
│   ├── ai/                       # Layer 6 (optional): provider-independent adapters; imports contracts only
│   ├── cli/                      # Layer 7: argparse entry points
│   └── api.py                    # Layer 7: small stable public facade
├── data/
│   ├── raw/                      # immutable source files, never edited (checksummed)
│   ├── normalized/               # derived, regenerable from raw by code
│   └── releases/                 # versioned DatasetManifests + frozen derived tables
├── examples/                     # reproducible experiment configs + expected outputs
├── benchmarks/                   # scientific validation cases with known analytical/published answers
├── tests/
│   ├── software/                 # unit, property, CLI, schema tests
│   ├── scientific/               # analytical, invariant, reference-comparison tests
│   └── acceptance/               # end-to-end checks from section 9 of the brief
└── runs/                         # gitignored output of experiment executions
```

Do **not** pre-create empty packages. Each directory is created in the roadmap phase that first needs it.

## 4. Layers and dependency direction

Imports may only point **downward** (higher layer number imports lower). A same-layer import is allowed only where noted. This is enforced by a test (`tests/software/test_import_boundaries.py`) that parses imports with `ast`, so no extra dependency is needed.

| Layer | Package | May import | Must never import |
|---|---|---|---|
| 0 | `contracts`, `units` | stdlib, `pydantic`, `pint` | anything else in `ecobackfire` (`contracts` may import `units`) |
| 1 | `core` | layer 0, `numpy`, `scipy` | `models`, `evidence`, `experiments`, `ai`, network libraries |
| 2 | `models` | layers 0-1 | `experiments`, `discovery`, `ai`, `evidence` |
| 2 | `evidence`, `datasets` | layers 0-1 (`datasets` may import `evidence`) | `models`, `experiments`, `ai` |
| 3 | `experiments` | layers 0-2 | `discovery`, `verification` (to avoid cycles), `ai` |
| 4 | `verification` | layers 0-3 | `discovery`, `ai` |
| 4 | `discovery` | layers 0-3, and `verification` interfaces only for the labeling gate | `ai` |
| 5 | `reporting` | layers 0-4 | `ai` |
| 6 | `ai` | `contracts` only (plus network/provider SDKs) | everything else |
| 7 | `cli`, `api` | everything except provider SDKs directly | n/a |

Key consequences:

1. **The scientific core (layers 0-5) never imports `ai`.** The only place AI output enters is as a `Hypothesis` or `EvidenceRecord` object that is created in layer 6/7 and passed *into* the engine as data. The engine cannot tell where it came from except through the `origin` field, which is carried through to reports.
2. `experiments` does not import `verification`. The runner produces `ExperimentResult`; verification consumes results and may re-invoke the runner through a narrow `Runner` protocol defined in `core`. This keeps the graph acyclic.
3. Models are discovered through a registry (`models.registry`) keyed by `model_id@version`, so the runner depends on the `EnvironmentalModel` protocol, never on concrete models.

## 5. Component designs

### A. Scientific model engine (`units`, `core`, `models`)

Responsibilities: typed variables with units, model protocol, deterministic evaluation, validation, traces.

Contract (Python protocol, to be implemented in Phase 1):

```python
class Model(Protocol):
    spec: EnvironmentalModel            # id, version, declared inputs/outputs, assumptions (data, not code)
    def validate_inputs(self, inputs: Mapping[str, Quantity]) -> list[ValidationIssue]: ...
    def evaluate(self, inputs: Mapping[str, Quantity], *, trace: Trace | None = None) -> Mapping[str, Quantity]: ...
```

Rules:
- **Pure functions.** `evaluate` has no I/O, no global state, no randomness, no clock reads. Same inputs + same `spec.version` => bit-identical outputs on the same platform. [DECISION] Cross-platform float differences are handled by comparing with documented tolerances, not by promising bit equality across machines.
- **Units at the boundary.** Inputs arrive as `pint` quantities, are checked against declared dimensionality, and are converted to the model's canonical units *inside* the engine wrapper. Models do their math on floats in canonical units, with the conversion recorded in the trace. [PROVISIONAL: this trades some elegance for speed in sweeps of 10^5+ evaluations; vectorized evaluation is a model option.]
- **Validation has three tiers:** (1) *schema* (type, unit dimensionality), (2) *physical* (e.g., efficiency improvement in [0, 1), non-negative energy), (3) *applicability* (e.g., parameter outside the range the model's assumptions were declared for). Tier 1-2 failures raise; tier 3 produces a **warning that is propagated into the result and the report**, because exploring outside applicability is a legitimate discovery activity but must never be silent.
- **Functional unit and system boundary** are mandatory fields of every model spec. A result object carries them; comparing results with different boundaries raises unless the comparison is explicitly a boundary-sensitivity experiment.
- **Trace.** An optional `Trace` records ordered steps `(step_id, formula_text, inputs, output, units)`; it is on by default for single evaluations and off for large sweeps (re-run on demand for any specific point).

### B. Evidence and dataset pipeline (`evidence`, `datasets`)

Pipeline stages, each producing immutable artifacts:

1. **Acquire** (manual or adapter): save the raw source untouched under `data/raw/<source_id>/` with retrieval date, URL/DOI, license, SHA-256.
2. **Extract**: create `EvidenceRecord`s that point to a precise location (page/table/figure/row) in the raw source. Quotations are stored only where licensing permits; otherwise a location pointer and a short paraphrase.
3. **Normalize**: convert to canonical units; **keep the original value, unit, and conversion factor** on the record.
4. **Reconcile**: detect conflicts between records for the same variable and compatible scope (same variable, overlapping geography/time, non-overlapping uncertainty intervals). Conflicts are *reported*, never auto-resolved. The user or curator picks a record, a documented pooling rule, or keeps both as scenario alternatives.
5. **Release**: freeze a `DatasetManifest` (list of record IDs + hashes + schema version + build code version). Releases are append-only; fixing an error creates a new version.

Missing data: a variable with no acceptable evidence resolves to an explicit `MissingValue` sentinel that raises at model evaluation time unless the experiment configuration binds it to a user-supplied or illustrative value, which is then labeled as such in every output. There is no default-to-zero path anywhere.

Raw vs derived: `EvidenceRecord.kind` is `observation | reported_estimate | derived | simulated | user_supplied | illustrative`. Derived records must list their parent record IDs and the derivation code version.

### C. Experiment runner (`experiments`)

An experiment is a pure function of `(ExperimentConfiguration, model version, dataset release)`. Supported experiment kinds, introduced incrementally:

| Kind | Purpose | Technique | Phase |
|---|---|---|---|
| `compare` | baseline vs intervention at specified inputs | Single evaluation of two parameter sets, delta + ratio | 1 |
| `scenario` | named, discrete input sets | Loop of `compare` | 1 |
| `oat_sweep` | one-at-a-time sensitivity and 1-D reversal scan | Grid + interval refinement | 1-2 |
| `breakeven` | find parameter value where a decision metric crosses zero | Bracketing (`brentq`) after a grid scan to detect sign changes; refuses to report a root without a bracket | 1 |
| `grid_2d` | reversal region boundaries in two parameters | Regular grid, contour extraction of the sign change | 3 |
| `global_sensitivity` | ranking influence under joint uncertainty | Sobol/Saltelli or Morris via QMC with fixed seed | 4 |
| `monte_carlo` | uncertainty propagation | Seeded sampling from declared distributions; reports probability that the decision reverses, with Monte Carlo error | 4 |

Reproducibility: configs are validated, canonicalized, and hashed; stochastic experiments require a `seed` (no default seed from the clock); results store the config hash, model version, dataset release ID, code version (git commit or package version), and dependency versions.

Decision metrics are first-class: an experiment declares a `DecisionMetric` (for example `delta_annual_emissions < 0` means "intervention is beneficial") so that "reversal" and "break-even" have precise, testable meanings.

### D. Autonomous discovery engine (`discovery`)

Discovery is **search with a falsification gate**, not generation of claims. v1 strategies, all deterministic and simple:

1. **Reversal scan:** for each model with declared decision metrics, scan every input over its plausible evidence-backed range (and an extended range, flagged) for sign changes of the decision metric.
2. **Pairwise interaction scan:** 2-D grids over the top-k influential inputs (from OAT/Morris) to find reversal regions that single-variable scans miss.
3. **Boundary-variation scan:** re-evaluate the decision under alternate declared system boundaries or functional units to find boundary-dependent reversals.
4. **Threshold/anomaly detection:** detect non-monotonic response, discontinuities, or large curvature in sweeps via finite differences with explicit tolerances (no ML).
5. **Evidence-gap targeting:** rank inputs by (influence on the decision) x (evidence uncertainty or absence) to say *where better data would matter most*.

Every strategy emits `Hypothesis` objects (status `unverified`) into a queue. Ranking score = documented weighted function of `evidence_quality`, `testability`, `potential_impact`, `uncertainty`; the weights live in config, are published in the report, and the score is only used to order verification work, never to assert truth. A candidate cannot be published as anything other than `candidate` until `verification` has produced a passing `VerificationReport`.

Machine learning is **not** used in v1. A surrogate model (e.g., Gaussian process) is only considered if a model becomes too expensive to sweep, and must then be validated against the true model on held-out points.

### E. Scientific red team and verification (`verification`)

A registry of **checks**, each a function `(CandidateFinding, context) -> CheckResult(status, evidence, details)`:

| Check family | Examples |
|---|---|
| Dimensional/units | recompute with units stripped and re-attached; compare |
| Invariants | zero-change input yields zero delta; scaling laws; monotonicity where claimed; conservation (mass/energy) where a model declares it |
| Independent recomputation | second implementation of the same equation (closed form vs numeric; or independent code path) agrees within tolerance |
| Analytical/benchmark | model reproduces cases in `benchmarks/` with known answers |
| Edge/extreme values | zero, near-zero, very large, boundary-of-domain inputs; NaN/inf handling |
| Competing assumptions | alternate functional forms, alternate parameter values drawn from conflicting evidence records |
| Boundary change | widen/narrow system boundary; does the reversal persist? |
| Numerical stability | refine grids/tolerances; perturb inputs by epsilon; reversal location must be stable |
| Evidence-quality gate | reversal depends on a parameter that is illustrative or has no sourced evidence => finding is capped at "conditional" |

Outputs: a `VerificationReport` listing every check, pass/fail/inconclusive, and unresolved uncertainties. **Failed or skipped checks are recorded, not discarded.** The labeling gate is a pure function: `label = f(report)`, with documented rules in `scientific-method.md`.

### F. Research intelligence and optional AI adapters (`ai`)

Provider-independent protocols:

```python
class HypothesisProposer(Protocol):
    def propose(self, context: ProposalContext) -> list[Hypothesis]: ...   # origin="ai:<provider>:<model>"

class EvidenceExtractor(Protocol):
    def extract(self, source: RawSourceRef, targets: list[VariableRef]) -> list[EvidenceRecord]: ...

class LiteratureSearch(Protocol):
    def search(self, query: str) -> list[SourceCandidate]: ...
```

Rules: outputs must validate against the same `contracts` schemas; every AI-extracted `EvidenceRecord` carries `extraction_method="ai_assisted"`, a source location, and `human_reviewed=false` until a person flips it; AI-proposed hypotheses enter the queue as `unverified` and get no special treatment; the `ai` package has a `NullProvider` default so the system runs with nothing configured; provider SDKs are optional extras and are imported lazily inside adapter modules only. Prompts/responses are logged to the run directory for audit (with a switch to disable for privacy).

### G. Reporting, reproducibility, interfaces (`reporting`, `cli`, `api`)

- Every run writes `runs/<run_id>/`: `config.json`, `result.json`, `verification.json`, `manifest.json`, `report.md`, and (when relevant) trace and sweep tables as CSV.
- JSON files carry `schema_version`; JSON Schemas are exported from pydantic models into `docs/schemas/` and checked in CI for drift.
- Markdown reports are rendered from JSON by a pure function. The report always includes: question, boundary and functional unit, evidence table with provenance labels, assumptions, results, sensitivity, verification outcome including failures, limitations, and reproduction command.
- CLI (v1): `ecobackfire run <config.toml>`, `ecobackfire verify <run_dir>`, `ecobackfire report <run_dir>`, `ecobackfire reproduce <manifest.json>`, `ecobackfire models list|describe`, later `datasets`, `discover`.
- Public Python API (`ecobackfire.api`): `load_config`, `run_experiment`, `verify`, `render_report`, `reproduce`. Semantic versioning applies to this module only.
- Future UI boundary: a UI or HTTP service is a *client* of `api.py` and the JSON artifacts. Nothing in the engine may depend on it.

## 6. Execution flow (end to end)

```
config.toml ──► load/validate ExperimentConfiguration ──► resolve model@version, dataset release
      │                                                         │
      │                                              bind inputs (EvidenceRecords → Quantities,
      │                                              flag illustrative / user-supplied / missing)
      ▼                                                         │
 Experiment runner: compare → sweep → bracket → root-find ──────┘
      │  (every point: units check → validate → evaluate → trace)
      ▼
 ExperimentResult (+ CandidateFinding if a reversal/break-even is found)
      ▼
 Verification: invariants, independent recomputation, benchmarks, edge cases,
               competing assumptions, boundary change, stability, evidence gate
      ▼
 VerificationReport ──► labeling gate ──► finding label (never above what checks support)
      ▼
 Reporting: result.json, verification.json, manifest.json, report.md
```

Discovery runs the same flow with a strategy generating many configs/hypotheses and the verification stage applied to the top-ranked ones.

## 7. Cross-cutting rules

- **Determinism:** no `random` without an explicit seed; no `datetime.now()` in anything that is hashed (timestamps live only in manifests, outside the hashed content).
- **Errors:** invalid state raises typed exceptions (`UnitError`, `PhysicalConstraintError`, `MissingEvidenceError`, `BoundaryMismatchError`). Applicability issues are warnings stored on results.
- **Logging:** stdlib `logging`, never used as a data channel; results carry their own warnings.
- **Model authoring:** a new domain is a subpackage in `models/` containing a spec (`EnvironmentalModel` data), an implementation, benchmark cases, and a `README` describing assumptions. It registers itself in the registry. No engine code changes.
- **Provenance labels** (`measured`, `sourced`, `estimated`, `simulated`, `user_supplied`, `illustrative`) propagate through bindings into results so every output number can be traced to the class of evidence that fed it. A result is labeled with the *weakest* class among its influential inputs.

## 8. What is deliberately deferred

Time-resolved/dynamic models (beyond simple annual integration), geospatial data, multi-impact LCA across many categories, life-cycle inventory database integration (e.g., licensed LCI databases), a web UI, a database server, ML surrogates, and any multi-agent AI orchestration. Each has a trigger condition listed in `risks-and-open-questions.md`.
