# EcoBackfire

An independent environmental **stress-testing and discovery engine**. It looks for the conditions under which apparently sustainable technologies, products, policies, and recommendations fail, lose their benefit, or reverse, and it tries hard to disprove its own findings before reporting them.

Two modes, one engine:

1. **Autonomous discovery:** generate testable hypotheses, scan parameter spaces for break-even points and decision reversals, and push every candidate through falsification.
2. **User-directed investigation:** take a claim, decision, or intervention and systematically test its assumptions, boundaries, and uncertainties.

## Status

**Phase 2 vertical slice implemented and run.** One end-to-end experiment (efficiency improvement with direct rebound and embodied emissions) runs offline from a TOML config through a deterministic model, break-even search, independent verification, falsification attempts, a labeling gate, a Markdown + JSON report, and a reproducibility manifest. 136 automated tests pass.

**What this does and does not show.** All inputs are *illustrative placeholders, not evidence*. The main finding (a break-even demand elasticity of 1.1128 for the placeholder inputs) is labeled `conditional` by the engine, not verified. Nothing here is scientifically validated, novel, or a statement about any real product or policy. See `docs/phase-2-vertical-slice.md` for exactly what was demonstrated, assumed, and left unverified.

## Principles (short form)

Scientific core first and offline-capable; deterministic by default; evidence with provenance, units, boundaries, and uncertainty; no invented evidence (missing is never zero); falsification over confirmation; independent verification; AI optional and never a source of numerical truth; no infrastructure without a concrete requirement.

## Architecture overview

Layered Python package (`src/ecobackfire/`), imports point downward only (enforced by a test):
`contracts/units` → `core` → `models`, `evidence`/`datasets` → `experiments` → `verification`, `discovery` → `reporting` → `ai` (optional, contracts only) → `cli`/`api`.

Results are versioned JSON plus Markdown reports, each with a reproducibility manifest. A finding carries a label (`unverified_hypothesis` … `verified_internal`); only humans can assign `independently_reproduced` or `established`.

## Documents

| File | Contents |
|---|---|
| `docs/architecture.md` | stack, repo layout, layers, components, execution flow |
| `docs/data-contracts.md` | schemas, validation rules, versioning |
| `docs/scientific-method.md` | evidence standards, uncertainty, validation, falsification, labels |
| `docs/first-experiment.md` | efficiency-rebound break-even design (illustrative inputs; not novel) |
| `docs/phase-2-vertical-slice.md` | what was built and run, exact outputs, verification and falsification results, limitations |
| `docs/implementation-roadmap.md` | phases, dependencies, acceptance criteria, priority order |
| `docs/risks-and-open-questions.md` | risks, open decisions, triggers for deferred infrastructure |
| `pyproject.toml` | package metadata and tooling/dependency policy |

## Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest                                                           # 136 tests, no network needed
ecobackfire run examples/rebound_breakeven/e2_breakeven_eps.toml  # writes runs/<id>/{config,result,verification}.json, report.md, manifest.json
ecobackfire reproduce runs/e2_breakeven_eps-ec25be88/manifest.json
```

Requires Python >= 3.11 (tested only on 3.12.3, Linux). Dependencies: `pydantic`, `pint`, `numpy`, `scipy`.

## Next implementation task

Export and freeze the data contracts: generate JSON Schemas for the shipped contracts into `docs/schemas/`, add a schema-drift test and archived `1.0.0` fixtures that must keep loading (details and acceptance criteria in `docs/phase-2-vertical-slice.md` section 9).
