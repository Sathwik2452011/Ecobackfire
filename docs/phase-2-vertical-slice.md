# Vertical Slice: First Verified Experiment (efficiency rebound break-even)

This document describes what was **actually built and run**. It delivers roadmap "Phase 1" (the thin vertical slice; the project brief calls it "Phase 2" because the blueprint was Phase 1 of the brief). Everything quoted below was produced by the code in this repository; committed copies of the outputs are in `examples/rebound_breakeven/expected/` and a test (`tests/acceptance/test_expected_outputs.py`) fails if a fresh run stops matching them.

**Read this first:** every numerical input is an **illustrative placeholder, not evidence**. The results demonstrate that the engine works (it finds, checks, and honestly labels a break-even). They say nothing about any real product, policy, or technology. The engine's own label for the main finding is `conditional`, deliberately not `verified_internal`.

## 1. What was implemented

Scope was held to what one experiment needs. Modules (see `docs/architecture.md` for the layer rules, which a test enforces):

| Path | Content |
|---|---|
| `src/ecobackfire/errors.py` | typed exceptions (`UnitError`, `PhysicalConstraintError`, `MissingEvidenceError`, `EvidenceNotAvailableError`, `NumericalError`, `ConfigError`, ...) |
| `units/` | `pint` wrapper; `kgCO2e`/`gCO2e`/`tCO2e` declared as mass labels; rejects unknown and incompatible units |
| `contracts/` | pydantic schemas: `Binding`, `Uncertainty` (point / interval / unknown), `EnvironmentalVariable`, `ModelAssumptions`, `EnvironmentalModel`, `ExperimentConfiguration`, `ExperimentResult`, `VerificationReport`, `ReproducibilityManifest` |
| `core/` | `Model` protocol, `Trace`, input preparation (unit conversion, missing-data refusal, bounds, applicability warnings), canonical JSON + SHA-256 |
| `models/efficiency_rebound/` | the deterministic model (equations EQ1-EQ8), assumptions, exclusions, three system-boundary variants, two demand forms; model registry |
| `experiments/` | `find_breakevens` (grid scan + Brent refinement + bracket recording), runner for `compare`, `breakeven`, `oat_sweep` |
| `verification/` | independent references (closed form; 60-digit `Decimal` recomputation and bisection), 12 checks, the pure labeling gate |
| `reporting/` | Markdown report renderer, reproducibility manifest |
| `api.py`, `cli/` | `load_config`, `execute`, `reverify`, `reproduce`; CLI `run`, `verify`, `reproduce`, `models list/describe` |
| `benchmarks/rebound/cases.json` | 13 known-answer cases with the provenance of each expected value |
| `examples/rebound_breakeven/` | four experiment configs (E1, E2, E3, E7) and their expected outputs |
| `tests/` | 136 tests: 36 software, 81 scientific, 19 acceptance |

Deliberately **not** built: evidence/dataset pipeline, Monte Carlo and global sensitivity, 2-D grids, discovery strategies, AI adapters, UI, database. No dependency beyond `pydantic`, `pint`, `numpy`, `scipy` (runtime) and `pytest`, `hypothesis`, `ruff`, `mypy` (development).

### Deviations from the blueprint (none changes the architecture)

1. `errors.py` sits at the package root as a layer-0 module instead of inside `core` (so that `units` can raise typed errors without importing `core`).
2. `Uncertainty` implements `point`, `interval`, `unknown` only (the roadmap's Phase 1 scope); `uniform/triangular/normal/lognormal/ensemble` are deferred. `unknown` is distinct from `point`, as specified.
3. `EvidenceRecord`, `DatasetManifest`, `Hypothesis`, `CandidateFinding` are not implemented. `VerificationReport` carries the `claim` text and the label directly. A binding with class `measured` or `sourced` is **refused** with `EvidenceNotAvailableError` because no evidence store exists; this makes "sourced" impossible to fake in this phase.
4. `compare` also covers named scenarios (`[[scenarios]]` overrides); there is no separate `scenario` kind.
5. The CLI has no separate `report` command; `run` and `verify` regenerate `report.md`.
6. `ExperimentResult` carries no timestamps or runtime metadata so that `result.json` is byte-reproducible; volatile fields (time, platform, commit) live only in `manifest.json`.
7. Weakest provenance is computed over **all** inputs (the conservative rule from `scientific-method.md` for runs without influence analysis), not influence-weighted.
8. Two model options (`boundary_variant`, `demand_form`) have documented defaults; the resolved values are always written into the result and report, so they are never silent.
9. `pyproject.toml`: version 0.1.0, `line-length = 120`, ruff ignores for N80x/RUF00x (uppercase names mirror the equation symbols).
10. Design-doc extension: the closed form for the break-even emissions factor f\* and the saturation existence condition under boundary variants B2/B3 were derived and tested here; they were stated only partially in `first-experiment.md` section 3.3.

## 2. Install and run

Requires Python >= 3.11 (developed and tested only on Python 3.12.3, Linux). No network and no API key are needed at any point after the dependencies are installed.

```bash
git clone <repo> ecobackfire && cd ecobackfire
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest                                   # 136 tests
ecobackfire run examples/rebound_breakeven/e2_breakeven_eps.toml
ecobackfire reproduce runs/e2_breakeven_eps-ec25be88/manifest.json
```

Other commands:

```bash
ecobackfire run examples/rebound_breakeven/e1_compare.toml          # scenarios across elasticities
ecobackfire run examples/rebound_breakeven/e3_breakeven_f.toml      # break-even grid emissions factor
ecobackfire run examples/rebound_breakeven/e7_oat_breakeven_eps.toml  # OAT sensitivity of eps*
ecobackfire verify runs/<run_dir>                                   # re-run verification, rewrite report
ecobackfire models describe efficiency_rebound@1.0.0                # assumptions, equations, boundary
python -m ecobackfire ...                                           # same CLI
```

Each run writes `runs/<experiment_id>-<config-hash8>/` with `config.json`, `result.json`, `verification.json`, `report.md`, `manifest.json`. The run directory name is derived from the config hash, so the same config always lands in the same place. This procedure was executed from a fresh `git clone` in a fresh virtualenv: 136 tests passed, the experiment ran, and `reproduce` reported byte-identical artifacts.

## 3. The experiment (E2) and its exact output

Research question (unchanged from `first-experiment.md`): for an efficiency improvement, under what demand elasticity does absolute annual emissions stop falling once annualized embodied emissions are counted?

Configuration: `examples/rebound_breakeven/e2_breakeven_eps.toml`. Model `efficiency_rebound@1.0.0`, boundary B1, constant-elasticity demand, decision rule "beneficial iff dC < 0". Search variable `epsilon` over [0, 3], 301 grid points, xtol 1e-12.

| input | value | unit | provenance | uncertainty interval (illustrative) |
|---|---|---|---|---|
| E0 baseline energy | 3000 | kWh/yr | illustrative | [2000, 4500] |
| eta energy-intensity reduction | 0.30 | dimensionless | illustrative | [0.2, 0.4] |
| phi energy share of marginal cost | 0.80 | dimensionless | illustrative | [0.5, 1.0] |
| epsilon (value used only as the base point) | 0.5 | dimensionless | illustrative | [0, 1.5] |
| f emissions factor | 0.40 | kgCO2e/kWh | illustrative | [0.1, 0.8] |
| EM0 / EM1 embodied | 1000 / 1720 | kgCO2e | illustrative | [500, 1500] / [1000, 2500] |
| L amortization | 12 | yr | illustrative | [8, 16] |
| s_max (used only by falsification test F3) | 1.5 | dimensionless | illustrative | [1.05, 3.0] |

With these values, r = (1720 - 1000) / (12 x 0.4 x 3000) = 0.05.

**Result** (`expected/e2_breakeven_eps/result.json`): one break-even at

- **epsilon\* = 1.11275746800796**, bracket [1.11, 1.12] with g = -0.862 at the low end and +2.268 at the high end, 4 Brent iterations, residual 0.0, `beneficial_side = below` (the intervention reduces absolute emissions for epsilon < epsilon\*, and increases them above).

Calculation trace at the break-even point (equation ids from the model spec): pi = 0.76 (EQ1); service ratio S1/S0 = 1.357142857 (EQ2); E1 = 2850 kWh/yr (EQ3); C0 = C1 = 1283.333 kgCO2e/yr (EQ4); dC = 0 (EQ5); r = 0.05 (EQ6); rebound R = 0.8333 (EQ7); emissions per unit service, intervention / baseline = 0.7368 (EQ8).

Note the second functional unit in that trace: at the break-even, absolute emissions are unchanged, yet emissions **per unit of service** have fallen by about 26%. E1 shows the divergence: at epsilon = 1.5 and 2.0, absolute emissions rise (+127.8 and +314.3 kgCO2e/yr) while the per-service intensity ratio is 0.7285 and 0.7191. Whether that is a "backfire" depends on which functional unit the decision cares about, and on how the extra service is valued; the tool does not decide.

**Label produced by the verification gate: `conditional`** (not `verified_internal`), for three recorded reasons: the inputs are illustrative (F7), the reversal vanishes under saturating demand for s_max in {1.05, 1.2} (F3), and no break-even exists inside the search range at 28 of 128 corners of the illustrative uncertainty box (F5).

### Other runs (all illustrative)

| Run | Result |
|---|---|
| E1 `compare` (epsilon = 0, 0.5, 1.0, 1.5, 2.0) | dC = -300.0, -176.5, -34.7, +127.8, +314.3 kgCO2e/yr; beneficial for epsilon <= 1.0, not beneficial for 1.5 and 2.0 (consistent with epsilon\* = 1.113) |
| E3 break-even emissions factor (epsilon = 0.5) | f\* = 0.101499693 kgCO2e/kWh; not beneficial below, beneficial above (a very clean grid cannot repay the embodied emissions). Label `conditional`. Under B3 (operational only) the "break-even" is the trivial root f = 0 |
| E7 OAT sensitivity of epsilon\* over the illustrative intervals | phi swing 1.023 (epsilon\* 1.879 to 0.856); f 0.721; EM1 0.401; EM0 0.266; eta 0.206; E0 0.161; L 0.145. No label (no claim under test) |

## 4. Independent verification: method and results

Honest framing: the references below were written by the same author and share the equations EQ1-EQ8 with the production model. They therefore verify the **implementation and numerics**, not the **equations' correctness or realism**. A conceptual error in the equations themselves would pass all of them.

| Check | Method | Result for E2 |
|---|---|---|
| V1 closed form vs numeric | epsilon\* = [ln(1-r) - ln(1-eta)] / [-ln(1-phi eta)] versus the Brent root | closed form 1.1127574680079593 vs numeric 1.11275746800796; abs. difference 6.7e-16; passed |
| V2 independent recomputation | `Decimal` (60 digits, exp/ln instead of float pow, own grid, bisection root finder, no import of production code) at 31 points plus the root | max relative error 1.8e-16, reference found the same single root; passed |
| V3 invariants | eta = 0 gives E1 = E0 and dC = dEM/L; epsilon = 0 gives E1 = (1-eta) E0; dC non-decreasing in epsilon; dC scales linearly with (E0, EM0, EM1); dC linear in EM1 with slope 1/L (0 under B3); unit-converted bindings (MJ/yr, gCO2e/kWh, tCO2e, month) give identical canonical inputs | all hold; passed |
| V4 known-answer benchmarks | 13 cases in `benchmarks/rebound/cases.json`: hand-derived exact values (e.g. epsilon\* = 1 for r = 0, phi = 1, any eta; E1 = E0 at phi = 1, epsilon = 1), closed-form values, no-crossing and saturation cases, boundary variants | all 13 reproduced; passed |

Note on benchmark independence: the cases labeled "hand calculation" or "exact analytical" do not depend on repository code; the cases labeled "closed form" evaluate the same formula as V1.

Software-level evidence also includes property tests (Hypothesis) of three-way agreement (production vs closed form vs Decimal) over random (eta, phi, r), the existence condition (no break-even when r > eta), the break-even emissions factor, and monotonicity/scaling, plus 20 tests of the break-even algorithm itself.

## 5. Break-even algorithm testing

`tests/scientific/test_breakeven_algorithm.py` (20 tests) covers: a linear function with a known root; a decreasing function (beneficial side flips); two roots of a quadratic; a root exactly on a grid point; roots at the lower and upper endpoints; no-crossing in both directions (reported as "never/always beneficial" with a note, not as a negative or invented root); a discontinuity (flagged `discontinuity_suspected`, not reported as a clean root); invalid inputs (low >= high, fewer than 3 points, non-positive or non-finite tolerance, non-finite bounds) rejected with `ConfigError`; non-finite function values refused with `NumericalError`; 200 random linear functions recovered to 1e-9. Two tests pin down **known limitations**: an even-multiplicity root and a dip narrower than the grid spacing are missed by the scan, and the output note says so.

## 6. Falsification attempts and outcomes (E2)

| Attempt | What was done | Outcome |
|---|---|---|
| F1 threshold perturbation | move epsilon\* by +/-1e-3 and +/-1e-6 (relative); the decision must flip on the claimed sides | passed (4 points) |
| F2 counterexample search | 2000 points on a different (half-cell offset) grid; any sign change not explained by the reported roots, or any lost root, refutes | passed; one sign change, at the reported root |
| F3 alternative demand form | rerun with saturating demand for s_max in {1.05, 1.2, 1.5, 2.0, 3.0} (illustrative); compare with the analytical existence condition (1-eta) s_max > 1-r, i.e. s_max > 1.357 | **narrowed the claim**: the reversal vanishes for s_max = 1.05 and 1.2; persists for 1.5, 2.0, 3.0; numerics matched the analytical prediction in all five |
| F4 boundary variation | B1, B2 (old device's embodied emissions sunk), B3 (operational only) | passed: epsilon\* = 1.1128 (B1), 0.8362 (B2), 1.2997 (B3); pre-registered predictions (d) B2 <= B1 and (e) B3 >= B1 held |
| F5 uncertainty-box corners | all 2^7 = 128 corners of the 7 illustrative intervals | **narrowed the claim**: threshold ranges 0.239 to 2.940 where it exists in [0, 3]; no break-even in range at 28 corners. Corner enumeration is not a proof of bounds (monotonicity not established) |
| F6 extreme/invalid inputs | 19 cases: eta = 0, eta -> 1, eta = 1, phi -> 0, epsilon = 1e6, L = 0, NaN, inf, etc. | passed: finite result or typed error in every case (epsilon = 1e6 raises `NumericalError` for overflow instead of returning inf) |
| F7 evidence gate | provenance of every input | **limits the claim**: all nine bindings illustrative |
| F8 numerical stability | double grid density, tolerance /10, extend search range by 50% | passed: same root to 1e-9 |

Pre-registered predictions from `first-experiment.md` section 5: (a) monotone dC in epsilon: held (V3, property tests); (b) numeric = closed form to 1e-9: held (6.7e-16); (c) epsilon\* = 1 at r = 0, phi = 1: held (B-001, B-002, and property tests); (d) B2 < B1: held (0.836 < 1.113); (e) B3 >= B1: held (1.300 >= 1.113); (f) absolute emissions can rise while per-service intensity falls: held (E1).

**Red-team capability tests (mutation-style):** five deliberately seeded bugs (additive offset in dC, sign flip in dC, elasticity scaled by 0.9, phi ignored, boundary variant ignored) were each detected by at least one required check and were labeled `refuted` or `inconclusive`, never `conditional` or `verified_internal`. A sixth mutant hides a second sign change in a dip narrower than the scan grid; only F2 (offset dense sampling) finds it and the label is `refuted`. When the benchmark fixtures cannot be located (simulated in a test), V4 is `skipped` and the label is `inconclusive`. This is evidence the gate is not vacuous, from only six planted bugs; it is not a measured detection rate.

**Reachability of `verified_internal`:** a test shows the label is reachable only if inputs are non-illustrative, uncertainty boxes are narrow, and alternative demand forms keep the reversal. It is a test of the gate's logic with synthetic assumptions (inputs marked `user_supplied`), not a claim about any real study.

## 7. Demonstrated, assumed, and unverified

**Demonstrated by execution in this environment (Linux, Python 3.12.3, numpy 2.5.4, scipy 1.18.1, pint 0.26.1, pydantic 2.14.1):**
- 136 tests pass, including from a fresh clone and fresh virtualenv; `ruff` and `mypy` (strict on `contracts/` and `core/`) are clean.
- The model reproduces hand-calculated cases, the closed-form epsilon\* and f\* (property-tested), and a 60-digit independent recomputation.
- E2 yields epsilon\* = 1.11275746800796 for the illustrative inputs; the artifacts are byte-identical across runs and reproducible from the manifest; tampering with the config or stored results is detected by `reproduce`.
- Invalid units, missing inputs, explicit missing markers, out-of-bounds and non-finite values are rejected; `measured`/`sourced` claims are refused without an evidence store; nothing is silently zero.
- The core ran with socket creation disabled and provider credentials removed (acceptance test).
- Boundary and functional-unit changes visibly change reports and verdicts.

**Assumed (model and inputs, not tested against reality):**
- Constant-elasticity (or saturating) demand with a single own-price elasticity; direct rebound only; static prices and emissions factor; linear amortization of embodied emissions.
- All input values and their uncertainty intervals, and the saturation values used in F3, are illustrative placeholders.
- The model-declared validity ranges are modelling judgement, not evidence.

**Unverified / not done:**
- No real-world evidence has been collected or integrated; no source is cited; the one literature pointer in `first-experiment.md` remains an unverified lead.
- No prior-art review: the classical result epsilon\* = 1 (for r = 0 and phi = 1) is long known; **no novelty is claimed**.
- Model validity (do real services follow these demand forms?) is untested.
- Cross-platform and other-Python-version reproducibility is untested (only the recorded environment above).
- Mutation detection rate is unmeasured (six seeded bugs only).
- Influence-weighted provenance, interaction effects (OAT cannot see them), Monte Carlo, and global sensitivity are not implemented.
- The Decimal reference and closed form share the equations with the production model (common-mode risk).

## 8. Known limitations

- Break-even detection is grid-based; narrow or even-multiplicity roots can be missed (documented, tested, mitigated by F2 and F8, not eliminated).
- Corner enumeration (F5) assumes monotone response for its range claim.
- The OAT table uses intervals that are placeholders; its ranking shows how the model behaves, not which real input matters.
- `epsilon` definitions (short vs long run, direct vs indirect, per-service vs per-energy) are ambiguous in the literature; any future evidence record must state which one it supplies.
- Single impact category (GHG); embodied emissions carry a label (`kgCO2e` is a mass alias), not a separately enforced dimension.
- The welfare effect of increased service is out of scope; the report says so.

## 9. Next smallest implementation task

**Export and freeze the data contracts** (roadmap Phase 2, first item): generate JSON Schemas for `ExperimentConfiguration`, `ExperimentResult`, `VerificationReport`, `ReproducibilityManifest`, `EnvironmentalModel` into `docs/schemas/<Schema>-<version>.json`; add (1) a test that fails when a model's schema differs from the checked-in file without a version bump, and (2) archived `schema_version 1.0.0` fixtures (the committed `expected/` outputs can seed them) that must keep loading. Acceptance: both tests pass, a deliberate field rename is caught, and `docs/data-contracts.md` lists which fields of each shipped schema exist today. Estimated size: S.

After that, per the roadmap's priority sequence: verification generalization (mutation-rate measurement) before experiment depth, uncertainty propagation, and the evidence pipeline.
