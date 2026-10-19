# E7: one-at-a-time sensitivity of the break-even elasticity (illustrative inputs)

- Experiment: `e7_oat_breakeven_eps`  ·  model: `efficiency_rebound@1.0.0`  ·  kind: `oat_sweep`
- Config hash (sha256): `01332f16ebc637841603ca4285b8ba8ccef27b590969277833900082a383b76d`
- Package version: `0.1.0`

> **ILLUSTRATIVE — not evidence.** This result contains illustrative inputs. It demonstrates the method, not real-world conclusions, and says nothing about any real product, policy, or technology.

## Research question

Which inputs move the break-even demand elasticity most, over the (illustrative) interval ranges declared on each input?

## Model, functional unit, and system boundary

- **Model:** Efficiency improvement with direct rebound and embodied emissions (`efficiency_rebound@1.0.0`, status `draft`)
- **Boundary variant:** `B1`: operational + annualized embodied emissions; decision at end of life of the old device
- **Demand form:** `constant_elasticity`
- **Functional unit:** Annual delivered service of one representative user of the service class over one year. Primary metric: absolute annual emissions (kgCO2e/yr per user). Secondary metric: emissions per unit of service (intensity_ratio).
- **System boundary `B1` includes:** operational energy use; embodied emissions (amortized)
- **Excluded:** indirect rebound; end-of-life processing; grid dynamics; maintenance and replacement
- **Decision rule (`delta_emissions_beneficial`):** intervention is beneficial iff absolute annual emissions fall (dC < 0)

**Declared assumptions**

- A1: Constant-elasticity demand applied to the effective price per unit of service (EQ1-EQ2).
- A2: Only direct rebound is modelled.
- A3: Elasticity is symmetric and constant over the price change considered.
- A4: Energy price and emissions factor are constant over the amortization period.
- A5: Baseline and intervention deliver the same quality of service per unit.
- A6: Embodied emissions are amortized linearly over L with no maintenance or replacement emissions.
- A7: One emissions factor applies to all energy (no marginal/average distinction, no dispatch effects).

**Declared exclusions:** indirect and economy-wide rebound (re-spending of savings); grid response to changed load; marginal vs average emissions; end-of-life processing; maintenance and replacement emissions; impact categories other than GHG (CO2e); welfare effects of increased service

## Inputs and provenance

| input | value | unit | provenance | uncertainty |
|---|---|---|---|---|
| `E0` | 3000 | kWh/yr | illustrative | interval [2000, 4500] |
| `EM0` | 1000 | kgCO2e | illustrative | interval [500, 1500] |
| `EM1` | 1720 | kgCO2e | illustrative | interval [1000, 2500] |
| `L` | 12 | yr | illustrative | interval [8, 16] |
| `epsilon` | 0.5 | dimensionless | illustrative | interval [0, 1.5] |
| `eta` | 0.3 | dimensionless | illustrative | interval [0.2, 0.4] |
| `f` | 0.4 | kgCO2e/kWh | illustrative | interval [0.1, 0.8] |
| `phi` | 0.8 | dimensionless | illustrative | interval [0.5, 1] |
| `s_max` | 1.5 | dimensionless | illustrative | interval [1.05, 3] |

Weakest provenance class among inputs: **illustrative**. (Influence-weighted provenance is not yet implemented; the conservative all-inputs rule is used.)

**Warnings recorded by the engine:**
- bound but unused under the chosen options: ['s_max']

## Results

**Break-even search** over `epsilon` ∈ [0, 3] dimensionless (301 grid points, xtol 1e-12): classification `crossing`.

1 break-even point(s) found on the scan grid.

| epsilon at break-even | kind | bracket | residual g | iterations | beneficial side |
|---|---|---|---|---|---|
| 1.112757468 | crossing | [1.11, 1.12] | 0 | 4 | below |

Calculation trace at the first break-even point:

- EQ1: pi = 1 - phi*eta  →  pi=0.76
- EQ2: S1/S0 = pi^(-epsilon)  →  service_ratio=1.357142857
- EQ3: E1 = (1-eta)*(S1/S0)*E0  →  E1=2850
- EQ4: C0 = f*E0 + EM0e/L ; C1 = f*E1 + EM1e/L  →  C0=1283.333333, C1=1283.333333
- EQ5: dC = C1 - C0  →  dC=0
- EQ6: r = dEM/(L f E0); dC/(f E0) = (1-eta)*(S1/S0) - 1 + r  →  r=0.05, dC_normalized=-4.163336342e-17
- EQ7: R = 1 - (E0-E1)/(eta*E0)  →  rebound_R=0.8333333333
- EQ8: intensity_ratio = (C1/C0)/(S1/S0)  →  intensity_ratio=0.7368421053

**Sensitivity (one-at-a-time (OAT))**, target `breakeven` (dimensionless); base value 1.112757468. Ranges come from the sources listed (binding intervals here are illustrative placeholders).

| input | range source | range | target at low | target at high | swing |
|---|---|---|---|---|---|
| `phi` | binding_interval | 0.5 … 1 dimensionless | 1.8790528 | 0.8561903625 | 1.022862438 |
| `f` | binding_interval | 0.1 … 0.8 kgCO2e/kWh | 0.4865651049 | 1.20740761 | 0.720842505 |
| `EM1` | binding_interval | 1000 … 2500 kgCO2e | 1.299661286 | 0.89883721 | 0.4008240764 |
| `EM0` | binding_interval | 500 … 1500 kgCO2e | 0.9770818701 | 1.243562094 | 0.2664802237 |
| `eta` | binding_interval | 0.2 … 0.4 dimensionless | 0.9856433519 | 1.191540148 | 0.2058967958 |
| `E0` | binding_interval | 2000 … 4500 kWh/yr | 1.015583027 | 1.176129945 | 0.1605469181 |
| `L` | binding_interval | 8 … 16 yr | 1.015583027 | 1.160389853 | 0.1448068263 |

Skipped: epsilon: is the searched variable

OAT shows one-at-a-time influence only; it cannot reveal interactions between inputs.

## Verification and falsification

**Recommended label: `not_applicable`.** No candidate finding was produced, so nothing was labeled.

| check | family | status | summary |
|---|---|---|---|
| `V1_closed_form_vs_numeric` | closed_form | **not_applicable** | no break-even search in this experiment |
| `V2_independent_decimal_recompute` | independent_recomputation | **passed** | production matches 60-digit Decimal reference (31 comparisons, 15 reference roots) |
| `V3_invariants` | invariants | **passed** | all invariants hold |
| `V4_known_answer_benchmarks` | benchmarks | **passed** | 13 known-answer cases reproduced |
| `F1_threshold_perturbation` | falsification_perturbation | **not_applicable** | no break-even search in this experiment |
| `F2_counterexample_search` | falsification_counterexample | **not_applicable** | no break-even search in this experiment |
| `F3_alternative_demand_form` | model_form | **not_applicable** | no break-even search in this experiment |
| `F4_boundary_variation` | boundary | **not_applicable** | no break-even search in this experiment |
| `F5_parameter_uncertainty_corners` | parameter_uncertainty | **not_applicable** | no break-even search in this experiment |
| `F6_extreme_and_invalid_inputs` | edge | **passed** | 19 extreme/invalid input cases behave as specified (finite result or typed error) |
| `F8_numerical_stability` | stability | **not_applicable** | no break-even search in this experiment |
| `F7_evidence_quality_gate` | evidence_gate | **limits_claim** | inputs are illustrative and/or lack characterized uncertainty: the result demonstrates the method and cannot be a real-world conclusion |

**Label justification:**
- No candidate finding exists (no break-even/reversal was found or none was requested).

**Falsification attempts that did not refute the claim:**
- F6_extreme_and_invalid_inputs: 19 extreme/invalid input cases behave as specified (finite result or typed error)

**Falsification attempts that narrowed or refuted the claim:**
- F7_evidence_quality_gate: inputs are illustrative and/or lack characterized uncertainty: the result demonstrates the method and cannot be a real-world conclusion

**Unresolved uncertainties:**
- Prior-art review has not been performed (prior_art_status = not_reviewed): no novelty claim is made.
- Model-form adequacy against real data has not been assessed; constant-elasticity demand is an assumption.
- Indirect/economy-wide rebound, grid dynamics, maintenance and end-of-life emissions are excluded.
- Every input is an illustrative placeholder: no real-world evidence has been integrated.

## Limitations

- Direct rebound only; indirect and economy-wide rebound are excluded.
- Constant-elasticity (or saturating) demand is assumed; neither form has been checked against real data here.
- Static annual model: no change in prices or emissions factors over the amortization period.
- Embodied emissions are linearly amortized; no maintenance, replacement, or end-of-life emissions.
- Single impact category (greenhouse gases).
- Break-even detection is grid-based: even-multiplicity roots and sign changes narrower than the grid spacing cannot be excluded (dense re-sampling is used as a falsification attempt, not a proof).
- Software tests and model verification show the implementation solves the stated equations; they do not show that the equations describe any real system.
- No prior-art review has been done; no claim of novelty is made.

## Reproduction

```
ecobackfire run examples/rebound_breakeven/e7_oat_breakeven_eps.toml
```

`manifest.json` in the run directory records the code version, dependency versions, config hash, and artifact hashes; `ecobackfire reproduce <run_dir>/manifest.json` re-runs and compares them.
