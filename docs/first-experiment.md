# First Experiment: Does an Efficiency Improvement Still Cut Emissions After Demand Response and Embodied Emissions?

Status: **Design, with Phase 1 implementation of E1, E2, E3, E7 and falsification attempts F1-F7 below** (not yet: E6 2-D grid, E8 Monte Carlo, E9 global sensitivity, and the temporal variant listed below as F8). Note: the code's check ids differ slightly from section 7's numbering (code `F5` = uncertainty-box corners, code `F8` = numerical stability, code `F6` = extreme/invalid inputs); the mapping is in `docs/phase-2-vertical-slice.md` section 6; results and pre-registered-prediction outcomes are in `docs/phase-2-vertical-slice.md`). All numerical inputs in this document are **ILLUSTRATIVE — not evidence** unless and until real evidence records replace them (see §6). No citation below has been retrieved or verified by this project; items marked "lead" are unverified pointers from memory and must be retrieved and checked before use.

**Novelty statement:** None. The rebound effect (and the Jevons paradox) is a long-established research area, and the closed-form relationships below are standard consequences of constant-elasticity demand. This experiment is a **method demonstration** to exercise the architecture end to end. It is not a discovery and the report must not describe it as one. Prior-art status: `not_reviewed`.

## 1. Research question

> For an energy-efficiency improvement that lowers energy use per unit of service, **under what combinations of demand response (price elasticity of service demand), energy-cost share, grid emissions intensity, and embodied-emission burden does the intervention fail to reduce absolute annual emissions, compared with the baseline?**

Sub-questions (each answered by a dedicated experiment run):

- Q1. At what demand elasticity ε\* does the intervention reach break-even (ΔC = 0) for given other inputs?
- Q2. At what grid emissions intensity f\* does the intervention reach break-even for given demand response?
- Q3. How does the answer change when the system boundary changes (embodied emissions included/excluded; replace-at-end-of-life versus early replacement)?
- Q4. How does the answer change when the functional unit changes (absolute annual emissions versus emissions per unit of service)?
- Q5. Does the reversal depend on the demand functional form (constant elasticity versus saturating demand)?

Why this choice over alternatives: it has a closed-form solution (strong independent verification), few parameters (rigorous sensitivity analysis is feasible), clear boundary/functional-unit effects (exercises the contracts), and evidence classes that are well-defined even though numbers are not yet collected. Alternative considered: electric vs combustion vehicle break-even mileage. It is data-richer but depends heavily on life-cycle inventory data that cannot be validated offline in the first slice; it is a good **second** experiment.

## 2. Baseline, intervention, functional unit, boundary

- **Baseline (B):** an existing technology delivering a service (e.g., heating, lighting, driving, computing) with energy intensity e₀ = E₀/S₀.
- **Intervention (I):** an alternative delivering the same service with energy intensity e₁ = (1 − η) e₀, where η ∈ [0, 1) is the fractional reduction in energy per unit of service.
- **Functional unit (primary):** *annual delivered service of one representative user of the service class, over one year* (absolute annual emissions in kg CO₂e per user per year). **Secondary functional unit:** emissions per unit of service delivered. The two can give **opposite** verdicts; reporting both is mandatory.
- **System boundary B1 (default):** operational emissions of delivered energy (energy used × emissions factor) **plus** annualized embodied emissions of the device (embodied / lifetime), for a decision at end of life of the old device (both devices are "new purchases"). Excludes: indirect/economy-wide rebound (re-spending of savings), grid response to changed load, end-of-life processing, effects on other services, rebound in the embodied supply chain.
- **Boundary variants:**
  - **B2 (early replacement):** the old device is still functional and its embodied emissions are sunk: EM₀ is set to 0 in the comparison, the new device's embodied emissions are fully charged.
  - **B3 (operational only):** embodied emissions excluded entirely (EM₀ = EM₁ = 0).
- **Metric ("beneficial" decision rule):** the intervention is *beneficial* iff ΔC < 0 (absolute annual emissions fall). Marginal and tie cases (|ΔC| within tolerance) are reported as `indeterminate`.
- **Welfare caveat (printed in every report):** if demand rises, users also receive more service. "Not beneficial in absolute emissions" is **not** "bad for people." EcoBackfire evaluates environmental outcomes under the stated metric; it does not make welfare judgments.

## 3. Model (deterministic)

### 3.1 Variables

| Symbol | Meaning | Canonical unit | Physical bounds | Illustrative base value* |
|---|---|---|---|---|
| E₀ | baseline annual energy use (final energy) | kWh/yr | > 0 | 3000 |
| η | fractional reduction of energy per unit service | dimensionless | [0, 1) | 0.30 |
| φ | share of per-unit-service marginal cost that scales with energy | dimensionless | (0, 1] | 0.80 |
| ε | absolute own-price elasticity of service demand (ε ≥ 0) | dimensionless | ≥ 0 | scanned (0 to 2) |
| f | emissions factor of delivered energy | kg CO₂e/kWh | ≥ 0 | 0.40 |
| EM₀ | embodied emissions of baseline device | kg CO₂e | ≥ 0 | 0 under B1 baseline = EM₁ − 720, see below |
| EM₁ | embodied emissions of intervention device | kg CO₂e | ≥ 0 | |
| ΔEM | EM₁ − EM₀ (can be negative) | kg CO₂e | any | 720 |
| L | amortization period / device lifetime | yr | > 0 | 12 |
| s_max | saturation multiplier of service (alternative demand form only) | dimensionless | ≥ 1 | 1.5 |

\*Illustrative values chosen for convenient tests (they give r = 0.05), not for realism.

### 3.2 Equations (ids are cited in traces)

- **EQ1 (effective price ratio):** π = 1 − φ η
- **EQ2 (service response, constant elasticity):** S₁/S₀ = π^(−ε)
- **EQ2′ (alternative: saturating demand):** S₁/S₀ = min(π^(−ε), s_max)
- **EQ3 (energy):** E₁ = (1 − η) · (S₁/S₀) · E₀
- **EQ4 (annual emissions):** C₀ = f E₀ + EM₀/L, C₁ = f E₁ + EM₁/L
- **EQ5 (decision quantity):** ΔC = C₁ − C₀ = f E₀ [ (1 − η) π^(−ε) − 1 ] + ΔEM / L
- **EQ6 (dimensionless embodied burden):** r = ΔEM / (L f E₀), so ΔC / (f E₀) = (1 − η) π^(−ε) − 1 + r
- **EQ7 (rebound, reported for readers used to that metric):** R = 1 − (E₀ − E₁)/(η E₀), for η > 0 (operational energy only; note R → ε as η → 0 for φ = 1)
- **EQ8 (service-normalized intensity ratio):** (C₁/S₁)/(C₀/S₀), computed directly from C and S (secondary functional unit)

### 3.3 Closed-form break-evens (used for verification, not to replace the numeric search)

Setting ΔC = 0 in EQ6 with constant elasticity:

- **Break-even elasticity:** ε\* = [ ln(1 − r) − ln(1 − η) ] / [ −ln(1 − φ η) ]
  - Valid for 0 < η < 1, 0 < φ ≤ 1, r < 1.
  - **Existence in the physical domain:** a break-even at ε\* ≥ 0 exists iff r ≤ η. If r > η, the intervention is not beneficial even with zero demand response (the embodied burden outweighs the operational savings); the runner must report "no reversal in ε ≥ 0 (already non-beneficial)", not a negative ε\*.
  - Sanity limits: r = 0 and φ = 1 gives ε\* = 1 (the classical backfire threshold); r = 0 and φ < 1 gives ε\* = ln(1−η)/ln(1−φη) > 1.
- **Break-even grid intensity:** f\* = (ΔEM / L) / ( E₀ [ 1 − (1 − η) π^(−ε) ] ), valid when the bracketed term is positive (i.e., operational savings exist) and ΔEM > 0. If the bracketed term ≤ 0 (backfire in energy), the intervention is non-beneficial for every f ≥ 0 when ΔEM ≥ 0.
- **Saturation (EQ2′):** the reversal exists for some ε iff (1 − η) s_max + r > 1. If s_max < (1 − r)/(1 − η), the intervention is beneficial for **all** ε, which falsifies "backfire occurs for ε > ε\*" under saturation.

A pre-implementation numerical spot check of the ε\* formula against an independent root finder (SciPy `brentq`) agreed to ~10⁻¹² for these cases (η, φ, r): (0.2, 1, 0) → 1.0; (0.2, 0.5, 0) → 2.117905; (0.3, 0.8, 0.05) → 1.112757; (0.5, 1, 0.1) → 0.847997. These are *test fixtures for the future implementation*, derived from the equations above, not empirical results.

### 3.4 Declared assumptions (model must record)

1. Constant-elasticity demand with elasticity applied to the effective price per unit of service (EQ1–EQ2).
2. Only direct rebound is modeled; indirect and macroeconomic rebound are excluded.
3. Elasticity is symmetric and constant over the range of price change considered.
4. Energy price and emissions factor are constant over the amortization period (first slice). Grid decarbonization over time is a planned variant (§7, V-time).
5. The intervention and baseline deliver the same *quality* of service per unit.
6. Embodied emissions are amortized linearly over lifetime L; no replacement or maintenance emissions.
7. Emissions factor f applies to marginal and average energy alike (no grid dispatch effects).

## 4. Evidence requirements (to be collected; none is collected yet)

| Variable | Evidence needed | Preferred source classes | Status |
|---|---|---|---|
| ε (and φ) | Empirical own-price elasticities for the *specific* service class and region; study design, period, and whether short-run or long-run | Peer-reviewed empirical studies and reviews of direct rebound. *Lead (unverified, from memory; must be retrieved and checked):* Sorrell, Dimitropoulos & Sommerville (2009), a review of empirical direct-rebound estimates, *Energy Policy*. | not collected |
| η | Measured or rated efficiency improvement between *comparable* products, with test procedure | Standards/labeling databases; manufacturer test data (noting in-use gaps) | not collected |
| E₀ | Typical annual energy use for the service and region | National/regional statistics | not collected |
| f | Emissions factor of the relevant energy carrier, region, and year (average vs marginal stated) | National inventory or grid operator statistics; established international datasets with licenses noted | not collected |
| EM₀, EM₁, L | Cradle-to-gate embodied emissions per device and lifetime, method and database stated | Peer-reviewed LCAs; EPDs; open LCI data if licensing allows | not collected |
| s_max | Evidence on saturation of demand for the service | Usage-survey literature | not collected |

Collection rules: raw sources stored with checksums; each value entered as an `EvidenceRecord` with a source location; conflicts among studies are kept and used as competing parameter sets in verification, not averaged away. **Until these exist, every output of this experiment carries the illustrative banner.**

## 5. Experiment design

All experiments are configured by TOML files in `examples/rebound_breakeven/`, executed by `ecobackfire run`, and verified by `ecobackfire verify`.

| Run | Kind | What it does | Output |
|---|---|---|---|
| E1 | `compare` / `scenario` | B vs I at illustrative base values with ε ∈ {0, 0.5, 1.0, 1.5}; reports E₁, ΔC, R, intensity ratio | table with units and provenance labels |
| E2 | `breakeven` on ε | Grid scan of ε ∈ [0, 3] to detect sign change of ΔC, then `brentq` within the bracket | ε\* numeric, with bracket and tolerance |
| E3 | `breakeven` on f | Scan f ∈ [0, 1.2] kg/kWh at fixed ε | f\* numeric |
| E4 | `scenario` (boundary) | E2 under B1, B2, B3 | table showing ε\* by boundary; difference displayed in the report summary |
| E5 | `compare` (functional unit) | Absolute emissions vs per-service intensity across ε | table showing where the verdicts diverge |
| E6 | `grid_2d` (η × ε) | Reversal region boundary curve | CSV + boundary points |
| E7 | `oat_sweep` | OAT sensitivity of ΔC and ε\* to each input ±20% and to full plausible ranges | tornado table |
| E8 | `monte_carlo` (Phase 4) | Declared distributions on ε, f, ΔEM, η; n and seed fixed; reports P(ΔC > 0) with Monte Carlo SE | distribution summary |
| E9 | `global_sensitivity` (Phase 4) | Morris screening, then Sobol indices if interaction suspected | ranked influence |

Plausible ranges and distributions used in E7-E9 are **illustrative placeholders** until evidence records replace them, and are labeled so in the output.

**Pre-registered predictions** (written before implementation, to be compared with outputs): (a) ΔC is monotonically increasing in ε; (b) ε\* from E2 equals the closed form within 10⁻⁹ relative tolerance; (c) at r = 0, φ = 1, ε\* = 1 for every η; (d) B2 yields smaller ε\* than B1 for the same inputs; (e) B3 yields ε\* ≥ B1; (f) there are inputs where absolute emissions rise while per-service intensity falls (the Q4 divergence).

## 6. Verification plan

Independent methods (at least one is required; this slice uses all four):

1. **Closed form vs numeric root** (V1): ε\* (and f\*) from §3.3 versus the runner's bracketing root, tolerance 10⁻⁹ relative, over a fixed grid of (η, φ, r) plus 10⁴ property-based random tuples (`hypothesis`) inside the valid domain.
2. **Independent recomputation** (V2): a second implementation in `verification/reference/rebound_reference.py` that computes service response by solving the demand relation numerically (a fixed-point/root solve of S = S₀ (p₁/p₀)^(−ε)) and then energy and emissions from first principles, **written without importing the production model's code**.
3. **Invariants** (V3): η = 0 ⇒ E₁ = E₀ and ΔC = ΔEM/L; ε = 0 ⇒ E₁ = (1−η) E₀; ΔC strictly increasing in ε for f E₀ > 0; ΔC linear in ΔEM; scaling E₀ → kE₀ with ΔEM → kΔEM scales ΔC by k; unit-stripped recomputation equals the unit-aware result.
4. **Known-answer benchmarks** (V4): the four fixtures in §3.3 plus saturation edge cases, stored in `benchmarks/rebound/cases.json` with the derivation noted.

Software tests and scientific validation remain separate (see `scientific-method.md` §3): passing V1-V4 shows the *implementation* solves the stated equations; it does not show that constant-elasticity demand describes any real service.

## 7. Falsification attempt (red team, recorded in the VerificationReport)

For the finding "the intervention is non-beneficial for ε > ε\* under B1":

- **F1 threshold perturbation:** at ε\*(1 ± 10⁻³) the decision must change sign on the claimed sides.
- **F2 counterexample search:** sample the claimed region (ε > ε\*, other inputs within plausible ranges) with random, corner, and boundary-adaptive points; any point where ΔC < 0 is a counterexample that narrows the claim.
- **F3 alternative demand form:** rerun under EQ2′ with s_max drawn from the plausible range; record whether the reversal persists. Predicted result: **it can vanish** when s_max < (1 − r)/(1 − η). If so the finding is `conditional` on unsaturated demand.
- **F4 boundary variation:** rerun under B2 and B3; record shifts of ε\*.
- **F5 competing parameter values:** once evidence exists, rerun with each conflicting evidence record for ε, f, EM.
- **F6 extreme inputs:** η → 0, η → 1, φ → 0, ε very large, L tiny; verify finite outputs, domain errors raised correctly (e.g., r ≥ 1), and no spurious sign changes.
- **F7 evidence gate:** if ε, f, or ΔEM are illustrative, the label cannot exceed `conditional`.
- **F8 temporal variant (V-time, Phase 3):** let f(t) decline over the amortization period (scenario inputs, not forecasts); record how ε\* and the benefit shift. Required to test assumption 4.

## 8. Reproducible report (contents)

`report.md` and `result.json` contain: research question; baseline/intervention; functional units and both boundary variants; equations with ids; evidence table with provenance classes (and the illustrative banner); results for E1-E9 with units and uncertainty class; break-even points with brackets and tolerances; sensitivity ranking and method; verification table including failed and skipped checks; falsification log; label with plain-language definition; limitations (below); reproduction command, code version, dependency versions, config hash, seed.

**Limitations to print:** direct rebound only; constant-elasticity assumption; static grid and prices; no indirect rebound; amortized embodied emissions; single impact category (GHG); illustrative inputs; no prior-art review; not a recommendation about any real product.

## 9. Upgrade path to a real-data experiment

1. Pick one concrete service class and region (decision: [OPEN]; candidates: household space heating, residential lighting, passenger-vehicle travel).
2. Collect evidence for each row of §4 with raw sources preserved and licenses recorded.
3. Run prior-art review; record `prior_art_status`.
4. Re-run E1-E9 with evidence-backed distributions; include conflicts as competing parameter sets.
5. Only then may a report call any result `verified_internal`, and never `established` or `independently_reproduced` without human curation.
