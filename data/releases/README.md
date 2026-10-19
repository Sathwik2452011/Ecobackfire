# Dataset releases (local registry root)

Layout: `data/releases/<dataset_id>/<release_version>/dataset.json` — see `docs/datasets.md` for the schema and
`docs/dataset-import.md` for how real data enters.

**Contents (real data only):**

| Release | Variable | Records | Source | Status |
|---|---|---|---|---|
| `owid-electricity-carbon-intensity@1.0.0` | `f` | 5 (World, IN, US, DE, FR; 2024) | Our World in Data energy dataset (Ember data), pinned to upstream commit `f8de88a` | `sourced_unreviewed`, uncertainty unknown |

EcoBackfire does not fabricate data. Nothing synthetic or illustrative may be placed here; synthetic fixtures for
tests live in `tests/software/_dataset_fixtures.py` and in temporary directories, never here (a test enforces it).
A release contains only what a human put in, with the source actually consulted.

Releases are append-only: to correct an error, add a new version (and set `parent_release`); do not edit a
released file. The importer refuses to overwrite a release whose content differs, and `ecobackfire datasets
validate` re-hashes the pinned raw files, so silent edits are detectable. Each release directory also holds an
`import_report.json` (per-record raw line, raw-row hash and recorded transformations).
