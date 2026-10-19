# Importing real data (Evidence & Dataset Intelligence — real-data Step 1)

Status: implemented for **one** layout (wide table + codebook). First real release: `owid-electricity-carbon-intensity@1.0.0`.

## Commands
```bash
ecobackfire datasets import data/imports/owid-electricity-carbon-intensity-1.0.0.toml [--dry-run]
ecobackfire datasets list
ecobackfire datasets validate [<dataset_id>@<version> | <registry root>]
```
`--root` (default `data/releases`) and `--raw-root` (default `data/raw`) are options of `datasets`.
`import` validates everything first and writes nothing on any problem. `validate` checks schema, provenance, units
(via the registry load) and re-hashes every pinned raw file; exit code 2 on any failure.

## What the importer guarantees
- **No value transformation.** The stored value is the cell's decimal text parsed to float64 (`471.080` → `471.08`). The
  source unit is kept (`gCO2e/kWh`), not converted. Unit conversion to the model's canonical unit happens at parameter
  resolution (Step 2), where it will be recorded. Per record: `raw_value_text`, `raw_file`, `raw_line`,
  `raw_row_sha256`, `transformations`.
- **Pinned inputs.** Each raw file is pinned by SHA-256 in the spec; a mismatch is rejected.
- **Strict cells.** Plain decimals only. Empty, `NA`, `nan`, `inf`, scientific notation, thousands separators are rejected, never skipped.
- **Row selection.** Each `[[select]]` must match exactly one row; the row's ISO-3 cell is cross-checked against the spec.
- **Unit validation.** The spec repeats the source codebook's unit text verbatim (must equal the codebook cell); the unit must
  parse; against a target model the variable must exist, the unit must convert to the model's canonical unit, and the
  converted value must satisfy the model's physical bounds (the converted number is used for this check only).
- **Provenance validation.** The existing `EvidenceRecord`/`SourceRef` rules apply (source id, citation, URL, date; placeholder
  text rejected).
- **Deterministic.** Same spec + same raw bytes → byte-identical `dataset.json` and `import_report.json`. Record id =
  `<variable>.<geography>.<period>`. No clock, network or randomness.
- **Append-only.** Re-importing identical content is a no-op; different content under the same id@version is an error.

## What it does not do
- It cannot prove the raw file is the genuine upstream file. Upstream identity is recorded (commit, full-file hash, URL) and
  `data/raw/.../extract_excerpt.py` recreates the excerpts from a pinned checkout; setting `ECOBACKFIRE_OWID_CHECKOUT`
  makes a test verify the excerpt lines byte-for-byte against upstream (done once in this session: passed).
- It attaches **no review** and invents **no uncertainty**: records are `sourced_unreviewed` with `uncertainty = unknown`,
  so `is_evidence_backed` is `False` (reasons: not reviewed; uncertainty not characterized). That is the true state.
- One source per spec; no per-row citation or uncertainty (see "Next import contract").

## The first real release
`owid-electricity-carbon-intensity@1.0.0`: variable `f` (kgCO2e/kWh canonical), five records, year 2024:

| Geography | code | raw text (gCO2e/kWh) |
|---|---|---|
| World | GLOBAL | 471.080 |
| India | IN | 705.400 |
| United States | US | 383.780 |
| Germany | DE | 336.380 |
| France | FR | 40.480 |

Source: Our World in Data "Data on Energy" (legacy release), `owid/energy-data` at commit
`f8de88a4f6f9cab44f1cf606047cbd6c4414042c`, column `carbon_intensity_elec`; the OWID codebook attributes it to Ember Yearly
Electricity Data (2026) and Yearly Electricity Data Europe (2026). Defined by the source as greenhouse gases per unit of
**generated** electricity, lifecycle, in gCO2e/kWh. License as stated by OWID: CC BY (third-party terms apply; Ember's were not
checked here, so redistribution is recorded as unknown). The row selection was fixed before any model run.

### Limitations of this release (also stored in the records)
1. **Generation vs delivered.** The model's `f` is per kWh of final energy delivered; this variable is per kWh generated. Transmission/distribution losses are not represented. It is an *average*, not a marginal factor.
2. **Electricity only.** It applies to electricity-consuming devices.
3. **No uncertainty, one year.** The source gives none. The five rows are different geographies (scope variation), **not** disagreement between sources.
4. **Upstream status.** OWID labels this release as legacy / no longer updated and points to a successor dataset; this release is pinned to a commit.
5. **Unreviewed.** No human has compared the stored values with the source; the Ember primary data was not consulted independently.

## Exactly what real data is still needed (the rebound model has 9 inputs; this release supplies 1)
Nothing below is imported, and nothing below may be filled with invented values.

| Input | Needed evidence | Why it is not here |
|---|---|---|
| `epsilon` (own-price demand elasticity of the service; short/long-run and direct-only definitions must be stated) | Values with ranges, geography, period, method and definition from peer-reviewed rebound reviews/meta-analyses (e.g. work by Sorrell, Dimitropoulos & Sommerville; Gillingham, Rapson & Wagner; Greening et al. — citations given from memory, to be verified; no numbers were taken from them) | Publisher sites are not reachable from this environment; values must be transcribed from the papers with locators, by a person, then reviewed |
| `E0`, `eta`, `EM0`, `EM1`, `L` | One documented baseline/efficient device pair: annual energy use, fractional intensity reduction, embodied emissions of each device, lifetime. E.g. an official product/ecodesign preparatory study or a published product carbon-footprint report, with the same functional unit | No open machine-readable source was located and retrieved here |
| `phi` | Energy share of marginal service cost, derived from an official energy price and the device's energy use per unit service (a recorded derivation) | Needs an official price series plus the device data above |
| `s_max` | Only used by the alternative saturating demand form; may remain illustrative/assumed and flagged | — |

## Next import contract (proposal — NOT implemented)
Literature values need per-record `source_id`, `citation`, `url`/DOI, `locator` (table/page), `value` **or** `low`/`high`, `unit`,
`geography`, `period`, `methodology`, optional `uncertainty`, and conflicts between sources kept as separate records. The current
importer allows one source per spec and no uncertainty, so a "record table" layout (one CSV row = one fully-cited record) must be
added before such data can be imported. It would reuse the same validation, pinning, determinism and append-only rules.
