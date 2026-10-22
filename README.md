# EcoBackfire

### Finding when efficiency can backfire.

EcoBackfire is an evidence-aware scientific research engine that investigates a counterintuitive question:

> **When can improving efficiency actually increase total resource use or emissions?**

Instead of jumping directly from a question to a model-generated conclusion, EcoBackfire creates a transparent workflow:

**Question → Web Evidence → Human Review → Scientific Model → Candidate Discovery → Falsification → Robustness → Classification → Reproducible Results**

The goal is simple: **find potentially surprising effects, then try to break them.**

---

## 🚀 What EcoBackfire Does

EcoBackfire combines web-based evidence gathering with a deterministic scientific analysis engine.

A user can enter a research question, after which the system:

1. Identifies the parameters required by the model.
2. Searches the web for relevant evidence.
3. Extracts candidate values, units, sources, and supporting text.
4. Presents the evidence to the user for review.
5. Allows the user to accept a source, choose between conflicting sources, provide an override, or mark evidence as unavailable.
6. Runs the actual EcoBackfire scientific engine using the reviewed inputs.
7. Searches the parameter space for potential backfire conditions.
8. Attempts to falsify discovered candidates.
9. Performs configured robustness/sensitivity analysis.
10. Classifies each candidate according to the available evidence.
11. Records provenance and generates reproducible artifacts.

The system deliberately separates:

- **Evidence** — what external sources actually provide.
- **Assumptions** — values supplied or selected by the user.
- **Illustrative values** — values used for exploration but not presented as real-world evidence.
- **Model results** — what the mathematical model calculates.

---

## 🔬 What Is a Candidate?

A **candidate** is a model-derived case where EcoBackfire finds a potential backfire condition.

For example, the engine may discover a candidate such as:

```text
epsilon#0
ε = 1.19486
```

This does **not** mean that the backfire is “1.19486 times worse” or that emissions increase by 1.19486%.

Instead, ε is a model parameter describing demand responsiveness. EcoBackfire has found that, under the other declared assumptions, a value around this threshold produces a potential backfire condition.

Candidates are therefore **possible model cases**, not automatically verified real-world events.

---

## 🧪 Falsification

Finding a mathematical condition isn't enough.

EcoBackfire actively attempts to challenge discovered candidates.

The falsification stage perturbs model inputs and checks whether the candidate still satisfies the relevant conditions.

The goal is not simply:

> “Can we find a result?”

but:

> **“Can we find a result that survives attempts to disprove it?”**

---

## 🛡️ Robustness

Robustness asks whether a candidate survives reasonable changes to its inputs.

For example, EcoBackfire can perturb configured parameters around their baseline values and rerun the analysis.

A candidate that survives those tests is more stable **within the tested perturbation range**.

This does not mean the candidate is proven true.

The current robustness analysis is intentionally limited: configured perturbations are applied **one input at a time**, so interactions between multiple simultaneously changing inputs are not tested.

---

## 🌐 Evidence-Aware Research

EcoBackfire can search the web for model parameters and show the resulting evidence before running the scientific analysis.

Each researched candidate can include:

- Value
- Unit
- Source
- URL
- Verbatim supporting text
- Evidence status
- Alternative sources

Conflicting sources are **not silently resolved**.

The user must make an explicit decision.

This prevents the research layer from quietly turning an uncertain web result into a supposedly authoritative model input.

---

## 👤 Human-in-the-Loop

EcoBackfire does not automatically decide which evidence is correct.

For each parameter, the user can:

- Accept a researched value
- Choose between conflicting sources
- Supply an override
- Use an illustrative value when permitted
- Mark the parameter unavailable

Overrides remain explicitly marked as **user-supplied assumptions** rather than being presented as external evidence.

A final confirmation is required before the scientific engine runs.

---

## 📊 Evidence Status

One of EcoBackfire's most important distinctions is:

**Lack of evidence ≠ evidence of failure.**

A candidate may be mathematically valid but still receive:

```text
evidence_insufficient
```

This means the system does not currently have enough documented evidence to support or challenge the candidate.

It does **not** mean:

```text
false
```

This distinction keeps model-derived findings separate from empirical claims.

---

## 🔁 Reproducibility

EcoBackfire records the configuration and provenance associated with a run and generates artifacts including:

```text
result.json
verification.json
manifest.json
report.md
config.json
```

The system can reproduce a completed mission and check whether the generated artifacts remain identical.

A successful reproduction is reported as:

```text
reproduced_exactly
```

This makes the result auditable rather than treating the output as a one-time chatbot response.

---

## 🖥️ Web Demo

The web interface provides the complete research workflow:

```text
1. Enter a research question
        ↓
2. Research required parameters
        ↓
3. Review sources and values
        ↓
4. Resolve conflicts / provide overrides
        ↓
5. Confirm the inputs
        ↓
6. Run EcoBackfire
        ↓
7. Discover candidates
        ↓
8. Falsify and stress-test candidates
        ↓
9. Review evidence and provenance
        ↓
10. Inspect reproducible results
```

The web layer uses the same underlying `run_mission()` engine as the CLI rather than implementing a separate calculation path.

---

## 🧩 Architecture

At a high level:

```text
                    ┌──────────────────┐
                    │   User Question  │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │   Web Research   │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │ Evidence Review  │
                    │   + Overrides    │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │   Mission /      │
                    │ Input Resolution │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │ EcoBackfire Core │
                    └────────┬─────────┘
                             ↓
              ┌──────────────┼──────────────┐
              ↓              ↓              ↓
        ┌──────────┐   ┌────────────┐  ┌────────────┐
        │Discovery │   │Falsification│  │ Robustness │
        └────┬─────┘   └──────┬─────┘  └──────┬─────┘
             └──────────────┬─┴───────────────┘
                            ↓
                    ┌──────────────────┐
                    │   Classification │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │ Provenance +     │
                    │ Reproducibility  │
                    └──────────────────┘
```

---

## 🛠️ Built With

- **Python**
- **FastAPI**
- **NumPy**
- **SciPy**
- **Pydantic**
- **Pint**
- **Tavily / Brave-compatible web research**
- **Vercel**
- **GitHub**

---

## 🧪 Testing

The project includes tests covering the scientific engine, evidence resolution, discovery, falsification, robustness, provenance, reproducibility, and the web workflow.

The test suite also includes checks for important failure modes such as:

- Conflicting evidence
- Missing evidence
- Invalid units
- Geographic mismatches
- Invalid periods
- Withdrawn/deprecated evidence
- User overrides
- Forged evidence reports
- Path traversal
- Missing confirmation
- Invalid numerical inputs
- Reproducibility

The project also uses deterministic checks to ensure that reproducible runs produce byte-identical artifacts where expected.

---

## ⚠️ Current Limitations

EcoBackfire is an active research prototype.

Current limitations include:

- Real-world evidence coverage is still limited for some parameters.
- Web extraction is currently based on structured/keyword-and-unit rules rather than a fully semantic scientific extraction system.
- Web-sourced values are treated as `sourced_unreviewed` until appropriately reviewed.
- Robustness currently uses configured one-at-a-time perturbations.
- Multi-parameter interactions are not yet part of the robustness analysis.
- A lack of evidence can result in `evidence_insufficient`.
- Research results should be reviewed by the user before being treated as empirical claims.

These limitations are intentional parts of the system's transparency rather than being hidden from the user.

---

## 🔮 What's Next?

Future development will focus on:

- Better scientific evidence extraction
- Published uncertainty and range handling
- Multiple real-world sources for the same parameter
- Evidence-sensitive candidate discovery
- Richer uncertainty propagation
- Multi-parameter robustness testing
- Stronger connections between evidence, falsification, and discovered candidates
- More scientific models beyond the current rebound/backfire analysis

The long-term goal is to make EcoBackfire a general research framework for discovering and rigorously testing **counterintuitive effects in real-world systems**.

---
## 🤖 AI Disclosure

AI tools were used during development to assist with coding, debugging, documentation, and UI development. The scientific model, analysis workflow, and project decisions were reviewed and validated by the team.

## 📌 Philosophy

EcoBackfire is built around a simple principle:

> **Don't just find a result. Find a result, expose its assumptions, show its evidence, and try to break it.**

A model can produce a mathematically interesting answer without that answer being supported by reality.

EcoBackfire is designed to keep those two things separate.
