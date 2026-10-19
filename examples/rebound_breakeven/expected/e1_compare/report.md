# E1: baseline vs intervention across demand elasticities (illustrative inputs)

- Experiment: `e1_compare`  ·  model: `efficiency_rebound@1.0.0`  ·  kind: `compare`
- Config hash (sha256): `cd85ea318b44c755d0f1a53ff45465b67cc3926d1db0b270d1161aa8033fced3`
- Package version: `0.1.0`

> **ILLUSTRATIVE — not evidence.** This result contains illustrative inputs. It demonstrates the method, not real-world conclusions, and says nothing about any real product, policy, or technology.

## Research question

How do absolute annual emissions and emissions per unit of service change between baseline and intervention as demand elasticity rises?

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

| scenario | overrides | E1 (kWh/yr) | dC (kgCO2e/yr) | decision (absolute emissions) | rebound R | intensity ratio (per unit service) |
|---|---|---|---|---|---|---|
| eps_0p0 | epsilon=0 | 2100 | -300 | beneficial | 0 | 0.7662337662 |
| eps_0p5 | epsilon=0.5 | 2408.865206 | -176.4539177 | beneficial | 0.3431835618 | 0.7519130673 |
| eps_1p0 | epsilon=1 | 2763.157895 | -34.73684211 | beneficial | 0.7368421053 | 0.7394285714 |
| eps_1p5 | epsilon=1.5 | 3169.559481 | 127.8237924 | not_beneficial | 1.188399423 | 0.7285448402 |
| eps_2p0 | epsilon=2 | 3635.734072 | 314.2936288 | not_beneficial | 1.706371191 | 0.7190566234 |

**Functional-unit divergence:** in scenario(s) eps_1p5, eps_2p0 absolute annual emissions RISE while emissions per unit of service FALL. Which functional unit is the right one is a decision framing, not a modelling result. Higher service is also a welfare gain; this tool does not weigh welfare.

## Verification and falsification

**Recommended label: `not_applicable`.** No candidate finding was produced, so nothing was labeled.

| check | family | status | summary |
|---|---|---|---|
| `V1_closed_form_vs_numeric` | closed_form | **not_applicable** | no break-even search in this experiment |
| `V2_independent_decimal_recompute` | independent_recomputation | **passed** | production matches 60-digit Decimal reference (25 comparisons, 0 reference roots) |
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
ecobackfire run examples/rebound_breakeven/e1_compare.toml
```

`manifest.json` in the run directory records the code version, dependency versions, config hash, and artifact hashes; `ecobackfire reproduce <run_dir>/manifest.json` re-runs and compares them.
