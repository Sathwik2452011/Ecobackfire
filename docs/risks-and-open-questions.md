# Risks and Open Questions

Severity: H/M/L. Likelihood: H/M/L. Items marked **[OPEN]** need a decision by the project owner.

## 1. Scientific risks

| ID | Risk | Sev/Lik | Mitigation |
|---|---|---|---|
| S1 | **Verified-looking but wrong model.** Software tests pass while the equations misrepresent reality. | H/M | Separate software, model verification, and validation (`scientific-method.md` §3); no blanket "validated" status; per-benchmark validation statements; human review of model specs. |
| S2 | **Overclaiming novelty.** Rebound is a mature field; most simple reversals are already known. | H/H | `prior_art_status` field; forbidden-phrase linter; first experiment explicitly non-novel; treat discovery value as *systematic coverage and rigor*, not novelty. |
| S3 | **Data-dredging.** Broad scans over many parameters will find "reversals" by chance or in implausible ranges. | H/H | Report number of hypotheses tested; flag extrapolation outside plausible ranges; pre-registered scan configs; require evidence-backed ranges for publishable labels. |
| S4 | **Model-form uncertainty is invisible to parameter sampling.** | H/M | Alternative formulations and boundary variants are part of the standard verification profile; label depends on them. |
| S5 | **Boundary and functional-unit choices drive conclusions.** | M/H | Mandatory boundary/functional unit; both functional units reported; boundary-variant experiments. |
| S6 | **Evidence heterogeneity.** Elasticities, emissions factors, and embodied emissions vary by region, period, method; naive pooling misleads. | H/H | Conflicts retained, scope metadata mandatory, scenarios over averaging; no auto-resolution. |
| S7 | **Elasticity ≠ rebound confusion.** Literature uses differing definitions (short vs long run, direct vs indirect, per-service vs per-energy). | M/H | Define ε precisely in the model spec; require evidence records to state the definition; reject mismatched definitions as applicability warnings. |
| S8 | **Marginal vs average emissions factors** change conclusions. | M/M | Record which is used; make it a boundary variant. |
| S9 | **Single impact category (GHG) hides trade-offs** (water, materials, toxicity). | M/H | Declared exclusion; roadmap for multi-impact models after P10. |
| S10 | **Normative misreading.** "Backfire" in emissions may coincide with welfare gains. | M/M | Welfare caveat in every report; avoid recommendation language. |

## 2. Engineering risks

| ID | Risk | Sev/Lik | Mitigation |
|---|---|---|---|
| E1 | **Over-building before one slice works.** | H/M | Roadmap forces thin vertical slice; no empty placeholder packages. |
| E2 | **Schema churn** as real needs appear. | M/H | SemVer for schemas, archived fixtures, migrations; Phase 1 implements minimal subsets. |
| E3 | **Floating-point reproducibility** across platforms/library versions. | M/M | Document tolerance instead of promising bit equality across machines; byte-identical only on same platform+lockfile; record dependency versions. |
| E4 | **pint overhead** in large sweeps. | L/M | Convert once at the boundary; model math in canonical floats; vectorize. |
| E5 | **Circular dependencies** erode layering. | M/M | AST-based import-boundary test from Phase 1. |
| E6 | **Dependency supply-chain/security** for a research tool. | L/L | Minimal core deps, pinned lock file, optional extras isolated. |
| E7 | **Verification suite becomes self-fulfilling** (checks written to pass). | H/M | Mutation testing with seeded bugs; independent reference implementation written separately; human review of check definitions. |
| E8 | **Illiquid solo capacity.** Evidence curation (P6-P7) is labor-intensive. | M/H | Keep evidence scope tiny per experiment; manual templates before automation. |
| E9 | **Equation-hash guard is weak** (hashes text, not code). | L/M | Benchmarks are the real guard; revisit if drift occurs. |

## 3. AI-related risks

| ID | Risk | Mitigation |
|---|---|---|
| A1 | Hallucinated citations or values | Citations are unverified leads until a human confirms; `human_reviewed=false`; extracted values require location pointers; fixtures with fake citations in tests. |
| A2 | AI-proposed hypotheses crowd the queue with plausible nonsense | Same gate as any hypothesis; ranking by testability and evidence, not by source. |
| A3 | Privacy/licensing when sending sources to providers | Opt-in; log what is sent; respect license `allows_quotation` and redistribution flags. |
| A4 | Provider lock-in | Protocols + `NullProvider`; no provider types in contracts. |

## 4. Product and legal risks

| ID | Risk | Mitigation |
|---|---|---|
| P1 | Users read output as authoritative advice about a product or policy | Banners, labels with plain-language definitions, explicit limitations, no recommendation language. |
| P2 | Defamation/misleading claims about specific products or companies | Reports describe model conditions, not product verdicts; real-product studies require evidence review before publication. [OPEN: publication policy] |
| P3 | Data licensing (some datasets prohibit redistribution) | License captured per source; dataset manifests flag non-redistributable items; keep such raw files out of the repo. |
| P4 | Scope creep into a general LCA tool | Non-goal in v1; integrate with, don't replicate, existing LCA software if multi-impact is needed. |

## 5. Open questions (decisions needed)

1. **[OPEN] Target service/region for Experiment 1 real-data version** (Phase 7): heating, lighting, vehicles, other? Affects evidence availability and licensing.
2. **[OPEN] Provenance-class ordering** for weakest-link labeling (e.g., where `user_supplied` ranks). Proposed default in `data-contracts.md`; owner may change.
3. **[OPEN] Mutation-test detection threshold** for verification suite quality (Phase 5).
4. **[OPEN] License for the repository and for derived datasets.**
5. **[OPEN] Publication policy:** what may be published externally and with which labels; who is the human curator for `independently_reproduced`/`established`.
6. **[OPEN] Minimum Python version** (3.11 proposed; confirm against your environment).
7. **[OPEN] Temporal modeling depth:** annual static (current plan) vs year-by-year dynamics for grid decarbonization (planned as variant V-time in P3).
8. **[PROVISIONAL] SALib vs own Sobol/Morris wrappers:** decide at Phase 4 after a small spike comparing accuracy and maintenance cost.
9. **[PROVISIONAL] argparse vs typer** for the CLI.
10. **[PROVISIONAL] Evidence entry method:** manual TOML/CSV templates first; whether any extraction automation is worth building depends on observed curation effort.

## 6. Triggers for adding deferred infrastructure

| Capability | Add only when |
|---|---|
| SQLite index | Listing/querying > ~1000 run directories becomes slow or error-prone |
| ML surrogate | A model's evaluation cost makes required sweeps infeasible AND held-out validation criteria are defined |
| Web UI / API server | A user who is not comfortable with the CLI needs access and the JSON artifacts are stable |
| Multi-agent AI workflow | An evaluated single-adapter workflow demonstrably fails on a named task |
| Geospatial data stack | A model requires spatially resolved inputs |
| Paid/licensed LCI database | A published-quality multi-impact model needs it and licensing/budget is resolved |
