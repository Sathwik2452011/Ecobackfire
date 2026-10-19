# Implementation Roadmap

Principle: each phase ends with something that runs, is tested, and can be reviewed before the next begins. Phase 1 is a **thin vertical slice** (one model, one experiment, one verification, one report), not a layer-by-layer build. Contracts are implemented only as far as the slice needs and extended in later phases (MINOR version bumps).

Size estimates are rough solo-developer effort: S ≈ 1-2 days, M ≈ 3-5 days, L ≈ 1-2 weeks. [PROVISIONAL]

## Dependency graph

```
P0 (this blueprint)
 └─ P1 vertical slice ──┬─ P2 hardening: units, uncertainty, manifests, reproduce
                        ├─ P3 experiment depth: 2-D grids, boundary/time variants
                        │        └─ P4 sensitivity + Monte Carlo
                        ├─ P5 verification suite generalization + labeling gate
                        └─ P6 evidence + dataset pipeline
                                 └─ P7 real-data version of experiment 1
 P5 + P4 ──► P8 discovery v1 (deterministic strategies)
 P1.. ──► P9 optional AI adapters (any time after P6; independent of P8)
 P7 + P8 ──► P10 second domain (proves extensibility)
 P2.. ──► P11 API/UI boundary (only if needed)
```

## Phase 1: Tiny end-to-end experiment (FIRST CODING PHASE) — size M

**Status: DONE (see `docs/phase-2-vertical-slice.md`; deviations listed there). Acceptance criteria 1-9 are covered by `tests/acceptance`, `tests/scientific` and `tests/software`; 136 tests pass. Phases 2 onward are not started.**

**Goal:** from a TOML config, run the rebound model, find ε\* by bracketing, verify against closed form and a second implementation, and write `result.json`, `verification.json`, `report.md` with the illustrative banner.

Scope (create only what is needed):
- `pyproject.toml` activated; `src/ecobackfire/` with `contracts/` (Uncertainty point/interval only, EnvironmentalVariable, ModelAssumptions, EnvironmentalModel, ExperimentConfiguration, ExperimentResult, minimal VerificationReport, minimal ReproducibilityManifest), `units/`, `core/` (Model protocol, Trace, errors), `models/efficiency_rebound/`, `experiments/` (`compare`, `breakeven`), `verification/` (V1-V4 for this model + labeling gate v0), `reporting/`, `cli/` (`run`, `verify`, `report`, `models describe`).
- `benchmarks/rebound/cases.json`, `examples/rebound_breakeven/*.toml`.
- Import-boundary test.

Acceptance criteria:
1. `ecobackfire run examples/rebound_breakeven/e2_breakeven_eps.toml` writes the run directory with all artifacts, offline, with no API key.
2. Running it twice yields byte-identical `result.json` (excluding metadata fields declared non-hashed).
3. ε\* numeric equals closed form within 1e-9 relative for all benchmark cases and 10⁴ hypothesis-generated tuples.
4. Mixing incompatible units (e.g., kWh bound to a kg variable) raises `UnitError`.
5. Unbound input without declared illustrative default raises `MissingEvidenceError`.
6. Case r > η returns "no reversal in domain; already non-beneficial", not a negative root.
7. Report contains the illustrative banner, boundary, functional unit, limitations, reproduction command; contains none of the forbidden phrases.
8. A deliberately broken model (wrong sign) fails V1/V2, and the labeling gate returns `refuted` or `inconclusive`, never `verified_internal` (mutation test).
9. `ruff`, `mypy` (strict on contracts/core), and `pytest` pass in CI/locally.

Out of scope: evidence pipeline, Monte Carlo, discovery, AI, datasets, `scenario`/`grid_2d`.

## Phase 2: Reproducibility and unit hardening — S/M
Depends on P1. Full `ReproducibilityManifest`, `ecobackfire reproduce`, git/dependency capture with dirty-tree flag, schema export + drift test, schema-version migration scaffold with archived fixtures, richer `Uncertainty` union.
Acceptance: reproduce from manifest on a clean checkout reproduces hashes; changing any dependency version or config changes the manifest; archived v1 fixtures still load.

## Phase 3: Experiment depth — M
Depends on P1. `scenario`, `oat_sweep`, `grid_2d`, boundary variants (B2, B3), saturation model form (EQ2′), time-varying emissions-factor variant (F8). Pre-registered predictions (a)-(f) become tests.
Acceptance: all pre-registered predictions are evaluated and recorded as pass/fail in scientific tests; F3 demonstrates the predicted vanishing of reversal under saturation; boundary changes appear in the report summary table.

## Phase 4: Sensitivity and uncertainty propagation — M
Depends on P2, P3. Seeded `monte_carlo`, Morris, Sobol (QMC), interval propagation, Monte Carlo standard errors, convergence checks.
Acceptance: Sobol indices on a linear test function match analytical values within stated tolerance; MC estimate of a known probability (e.g., normal tail) within 3 standard errors on 100 seeds; `unknown` uncertainty refuses propagation without a labeled assumption; reports show uncertainty class beside every number.

## Phase 5: Verification generalization — M
Depends on P1 (can run parallel to P3/P4). Registry of check families usable by any model that declares invariants and alternatives; counterexample search; stability checks; full labeling gate with documented rule table; forbidden-phrase linter for reports.
Acceptance: mutation tests (seeded model bugs) are caught at ≥ the rate you set as a project threshold [OPEN: pick, e.g. 90%]; every label transition rule from `scientific-method.md` §5 has a test; failed and skipped checks always appear in output.

## Phase 6: Evidence and dataset pipeline — L
Depends on P2. `EvidenceRecord`, `SourceRef`, raw source store with checksums, manual-entry CLI/templates (`ecobackfire evidence add` from a TOML/CSV template), unit normalization preserving originals, conflict detection, `DatasetManifest` build/verify, `MissingValue` semantics, bindings from records.
Acceptance: raw file tampering detected by checksum; normalization is reversible; conflicting records produce a `ConflictRecord` surfaced in reports; missing evidence never becomes zero (property test); dataset release hash is reproducible; licenses summarized and non-redistributable flags honored.

## Phase 7: Real-evidence version of Experiment 1 — M (mostly research work)
Depends on P4, P5, P6. Choose service class/region; collect and review evidence; run prior-art review; rerun E1-E9; produce first report whose label may exceed `conditional`.
Acceptance: every influential input has a sourced record with location pointers; conflicts handled; `prior_art_status` recorded; report states what is and is not claimed.

## Phase 8: Discovery v1 — L
Depends on P4, P5 (and P7 for non-illustrative results). Reversal scan, pairwise interaction scan, boundary-variation scan, threshold detection, evidence-gap targeting; hypothesis queue; ranking with published weights; verification gate on top-ranked candidates only.
Acceptance: on the rebound model, the scan *rediscovers* the known analytical reversal surface (planted-answer test); on a synthetic model with a planted hidden interaction, the interaction scan finds it and a main-effects-only scan does not; scans report number of hypotheses tested; no candidate is publishable without a VerificationReport.

## Phase 9: Optional AI adapters — M
Depends on P6 (evidence schema), independent of P8. `NullProvider`, protocols, one provider adapter behind an optional extra, structured-output validation, audit logs, `human_reviewed=false` enforcement.
Acceptance: entire test suite passes with the `ai` extra uninstalled; import-boundary test proves the core never imports `ai`; invalid/hallucinated-citation fixture is stored as an unverified lead and never reaches a `sourced` binding without human review; AI-generated hypothesis cannot set labels.

## Phase 10: Second domain — M/L
Depends on P7, P8. Add a model from a different domain (suggested: EV vs combustion break-even mileage, or reusable vs single-use packaging) **without modifying engine code**.
Acceptance: the only changes are inside `models/<new_domain>/`, `benchmarks/`, `examples/`, docs. If engine changes are needed, document why and revise the interface.

## Phase 11: API/UI boundary — S/M (conditional)
Only when a concrete user need appears. A read-only HTTP wrapper or static report viewer over `api.py` and JSON artifacts. No engine dependency on it.

## Prioritized implementation sequence

1. **P1** thin vertical slice (do this next).
2. **P2** reproducibility hardening (trust foundation).
3. **P5** verification generalization (prevents overclaiming before more features exist).
4. **P3** experiment depth (boundary/saturation/time variants make the first experiment scientifically interesting).
5. **P4** sensitivity and uncertainty.
6. **P6** evidence and dataset pipeline.
7. **P7** real-evidence Experiment 1.
8. **P8** discovery v1.
9. **P9** AI adapters.
10. **P10** second domain, then **P11** if needed.

Rationale: build the machinery that prevents false claims (P2, P5) before the machinery that generates claims (P8, P9).
