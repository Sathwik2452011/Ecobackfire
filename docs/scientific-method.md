# Scientific Method

This document defines what EcoBackfire is allowed to claim, and under what conditions. It is a policy document: the code in `verification/` and `reporting/` implements it, and tests check that the code cannot violate it.

## 1. Evidence standards

### 1.1 Provenance classes

| Class | Meaning | Required metadata |
|---|---|---|
| `measured` | Direct measurement with documented method (by the project or a cited dataset) | source, method, scope, uncertainty (or explicit `unknown`) |
| `sourced` | Value reported in a cited publication, report, or database, not independently re-derived | citation with location, retrieval date, license, scope |
| `estimated` | Derived by the project or a curator from sourced values using a documented procedure | parent record IDs, derivation description |
| `simulated` | Output of a model run (this project's or another's) | model id/version, configuration hash |
| `user_supplied` | Provided by the person running the experiment | author note; flagged in reports |
| `illustrative` | Placeholder chosen to exercise the engine; **not evidence** | the literal marker `ILLUSTRATIVE — not evidence` |

### 1.2 Rules

1. Every value used in a result has a class. There is no unclassified value and no default of zero.
2. A result is labeled with the **weakest class among its influential inputs** (influence determined by sensitivity analysis; if sensitivity has not been run, by the weakest class among *all* inputs).
3. Illustrative inputs are allowed in demonstrations and tests. A report that uses them carries a banner at the top: "Contains illustrative inputs; results demonstrate method, not real-world conclusions."
4. System boundary, functional unit, geography, and reference period of each evidence record are compared with the model's declared applicability. Mismatches create warnings in the result; they are never silently ignored.
5. Raw source files are preserved unmodified with checksums. Normalization is reversible: original value, unit, and conversion factor are retained.
6. Conflicting records for the same variable and overlapping scope are reported. Resolution options are: choose one with justification, pool using a documented rule, or keep both as scenarios. Conflict status appears in every report touching that variable.
7. No source is cited unless it was actually retrieved and located by a human or a verified script. Citations proposed by an AI system are treated as *unverified leads* until a curator confirms the source exists and says what is claimed; they are stored with `human_reviewed=false`.
8. Quoting follows license terms. Where quotation is not permitted, store the location pointer and a short paraphrase.

### 1.3 Evidence quality score (for ranking, not truth)

A transparent, documented heuristic combining: provenance class, whether a measurement method is documented, scope match with the model's applicability (geography/time/technology), recency relative to the technology's rate of change, presence of reported uncertainty, and whether multiple independent sources agree. The score only orders work; it never changes a numerical result. The exact weights live in a versioned config file and are printed in reports. [PROVISIONAL: weights to be tuned only through documented decisions, not by tuning to produce desired discoveries.]

## 2. Uncertainty

Types of uncertainty the engine distinguishes:

- **Parameter uncertainty:** the true value of an input is not known precisely. Represented with `Uncertainty` objects and propagated by Monte Carlo or interval analysis.
- **Model-form (structural) uncertainty:** the equations may be wrong or incomplete. Not representable by sampling parameters; handled by *alternative formulations* and boundary variants run side by side, and by listing exclusions. A result that survives only under one model form is labeled so.
- **Scenario uncertainty:** the future or context differs (grid mix, usage patterns). Represented as named scenarios, not as probability distributions, unless a source provides probabilities.
- **Variability:** real heterogeneity across households/regions/units. Distinguished from uncertainty in the schema (`scope`) and in reports; a distribution of variability is not an error bar.

Rules:

1. `unknown` uncertainty and `point` uncertainty are different. Propagation through `unknown` either refuses, or requires an explicit user-supplied assumption that is labeled in the report.
2. Interval analysis (bounds without distributions) is preferred when the only information is bounds. Do not turn bounds into a uniform distribution without saying so.
3. Monte Carlo results report: sample size, seed, method, and Monte Carlo standard error of reported probabilities. Probabilities of reversal are conditional on the assumed input distributions and are labeled as such.
4. Reports never present a single number without its uncertainty class (`point`, `interval`, `distribution`, `unknown`) next to it.
5. Sensitivity analyses state method and settings. OAT is used for transparency and 1-D reversal scanning; global methods (Morris screening, Sobol indices) are used when interactions are plausible. A conclusion that depends on OAT alone is labeled as such.

## 3. Validation

Software correctness and scientific validity are separate claims:

| Layer | Question | Evidence | Can pass while the other fails? |
|---|---|---|---|
| **Software verification** | Does the code do what the specification says? | unit, property, schema, determinism, CLI tests | Yes: code can faithfully implement a wrong model |
| **Model verification** | Does the implementation solve the stated equations correctly? | analytical solutions, independent second implementation, convergence/refinement tests, invariants | Yes |
| **Model validation** | Do the equations represent the real system adequately for the stated purpose and domain? | comparison with measured or published data, expert/literature review, independent models | Cannot be established by tests alone |

Statements allowed in reports:
- "The implementation matches the analytical solution within tolerance X." (model verification)
- "The model reproduces published benchmark B within Y%." (limited validation; only if B was actually obtained, documented, and cited)
- A model never receives a blanket "validated" status. It receives per-domain, per-benchmark validation statements with dates.

`EnvironmentalModel.status = reviewed` means: assumptions documented, benchmark and analytical cases pass, and a human has reviewed the specification. It does **not** mean "scientifically validated for all uses."

## 4. Falsification protocol

Given a candidate finding F (a claimed reversal or break-even), the red team executes, and records results of, at least:

1. **Recompute independently:** closed-form versus numeric root; or second code path; agreement within declared tolerance.
2. **Perturb the claim's boundary:** move the claimed threshold ±δ (relative and absolute). The decision must flip on the claimed side; if it doesn't, the threshold is wrong.
3. **Extreme and edge inputs:** zero, near-zero, upper physical bounds, values that make denominators vanish; check that outputs are finite, signs are sensible, and constraints raise errors.
4. **Alternative parameter values:** replace each influential input with every conflicting evidence record, and with the ends of its plausible range. Does the finding hold, narrow, or vanish?
5. **Alternative assumptions / model forms:** run declared alternative formulations (for example, a saturating demand function instead of constant elasticity). Record whether the reversal depends on the functional form.
6. **System-boundary variation:** widen/narrow boundaries (e.g., exclude embodied emissions; treat early replacement versus end-of-life replacement).
7. **Numerical stability:** halve tolerances, double grid density; the located threshold must stay within tolerance.
8. **Evidence-quality gate:** if the reversal condition depends materially on an illustrative, user-supplied, or unknown-uncertainty input, cap the label (see §5).
9. **Search for counterexamples:** deliberately sample the claimed region for points where the finding does not hold (random + corner + adaptive refinement near the boundary).
10. **Prior-art check (human-assisted):** record a review of the literature; until done, no novelty language.

All attempts, including those that *failed to refute* the finding, are stored in the `VerificationReport`. A finding that survives only because the red team was never run is `unverified`, not "survived".

## 5. Discovery classification and the labeling gate

| Label | Meaning | Who/what may assign it |
|---|---|---|
| `unverified_hypothesis` | Proposed statement with falsification criteria; no test yet | any generator (user, strategy, AI) |
| `candidate` | An experiment appears to support the hypothesis; verification not complete | experiment runner |
| `conditional` | Verified to hold *only* under stated assumptions or because key inputs are illustrative/unsourced | verification gate |
| `verified_internal` | All required checks passed under the profile; inputs adequately sourced; model-form sensitivity recorded | verification gate (pure function of the report) |
| `refuted` | At least one required falsification test refuted the claim as stated | verification gate |
| `inconclusive` | Required checks skipped, unstable, or contradictory | verification gate |
| `independently_reproduced` | Reproduced by a different implementation/dataset/person, documented | **human curator only** |
| `established` | Consistent with a body of peer-reviewed literature, reviewed by a human | **human curator only** |

Labeling function (summary of rules, evaluated in order):

1. Any `failed` check in a **required** family → `refuted` (if the failure refutes the claim) or `inconclusive` (if the failure is a numerical/stability problem).
2. Any required check `skipped`/`inconclusive` → `inconclusive`.
3. Any influential input with class `illustrative`, or `unknown` uncertainty with no user-supplied assumption → `conditional`.
4. Reversal appears only under one model form while an alternative form was declared and not run → `inconclusive`; run and no reversal → `conditional`.
5. Otherwise → `verified_internal`.

`verified_internal` is deliberately not "true". It says "this finding survived this project's own attempts to break it, with this evidence". Reports use the exact label text and a one-sentence plain-language definition next to it.

Language rules for reports and UI: no "proves", "discovered", "novel", "first", or "validated" unless the corresponding structured field supports it (`prior_art_status`, `independently_reproduced`, benchmark records). Tests grep rendered reports for forbidden phrases when the supporting fields are absent.

## 6. Research integrity practices

- **Pre-registration of search:** discovery runs record strategy, ranges, and thresholds *before* execution (in the config); post-hoc range tuning to produce reversals is detectable because configs are hashed and stored.
- **Multiple-comparison awareness:** broad scans produce many apparent reversals; reports state the number of hypotheses tested. Reversals found by scanning large ranges are labeled as *explored extrapolations* if outside the plausible evidence range.
- **Report negative results:** refuted hypotheses and failed checks stay in the archive and are summarized in reports.
- **Separation of roles in code:** generators cannot set labels; the gate cannot generate hypotheses; AI adapters cannot touch either.
- **Version everything:** model, dataset, code, config, schema; any change that could alter a result bumps a version.
- **Human review is explicit:** fields such as `human_reviewed` and curator-only labels exist so the system cannot imply review that did not occur.
