"""The REAL first evidence dataset: ``owid-electricity-carbon-intensity@1.0.0`` (model variable ``f``).

These tests read the committed release, its pinned raw excerpts and its import spec. Nothing here is synthetic.
They pin four things:
1. the stored values are exactly what the pinned source files say (no transformation, no tuning);
2. the release can be rebuilt byte-for-byte from the spec + raw files (reproducibility);
3. its evidence status is reported honestly (cited, unreviewed, no uncertainty -> NOT 'evidence-backed');
4. exactly which rebound-model parameters this release does and does not supply.

Optional upstream check: set ECOBACKFIRE_OWID_CHECKOUT to a checkout of https://github.com/owid/energy-data at
commit f8de88a4f6f9cab44f1cf606047cbd6c4414042c to verify every excerpt line is byte-identical to upstream.
"""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path

import pytest

from ecobackfire.datasets.ingest import build_release, load_import_spec, parse_decimal, verify_raw_files
from ecobackfire.datasets.registry import DatasetRegistry
from ecobackfire.evidence.records import evidence_backed_issues, is_evidence_backed
from ecobackfire.models.registry import get_model
from ecobackfire.units import to_canonical

ROOT = Path(__file__).resolve().parents[2]
DS, VER = "owid-electricity-carbon-intensity", "1.0.0"
SPEC = ROOT / "data" / "imports" / f"{DS}-{VER}.toml"
RAW = ROOT / "data" / "raw"
COMMIT = "f8de88a4f6f9cab44f1cf606047cbd6c4414042c"
MODEL = get_model("efficiency_rebound@1.0.0").spec

#: Values read by a human from the upstream CSV (the 'carbon_intensity_elec' cells), independent of the importer.
EXPECTED_G_PER_KWH = {"World": 471.08, "India": 705.4, "United States": 383.78, "Germany": 336.38, "France": 40.48}


@pytest.fixture(scope="module")
def registry() -> DatasetRegistry:
    return DatasetRegistry.from_directory(ROOT / "data" / "releases")


@pytest.fixture(scope="module")
def release(registry: DatasetRegistry):  # type: ignore[no-untyped-def]
    return registry.get(DS, VER).release


def test_values_equal_the_pinned_source_cells_and_nothing_else(release) -> None:  # type: ignore[no-untyped-def]
    assert len(release.records) == 5
    by_geo = {r.evidence.scope.geography: r for r in release.records}
    assert set(by_geo) == set(EXPECTED_G_PER_KWH)
    for geo, expected in EXPECTED_G_PER_KWH.items():
        r = by_geo[geo]
        assert r.evidence.value == expected  # exact: no scaling or rounding at import
        assert r.evidence.unit == "gCO2e/kWh"  # source unit preserved, not silently converted to kg
        assert parse_decimal(r.metadata["raw_value_text"]) == expected
        assert r.metadata["transformations"].startswith("none")


def test_each_record_traces_to_a_line_of_the_pinned_raw_excerpt(release) -> None:  # type: ignore[no-untyped-def]
    lines = (RAW / release.raw_files[0].path).read_text(encoding="utf-8").split("\n")
    header = next(csv.reader(io.StringIO(lines[0])))
    ci = header.index("carbon_intensity_elec")
    for r in release.records:
        n = int(r.metadata["raw_line"])
        cells = next(csv.reader(io.StringIO(lines[n - 1])))
        assert cells[header.index("country")] == r.evidence.scope.geography
        assert cells[header.index("year")] == r.evidence.scope.period == "2024"
        assert cells[ci] == r.metadata["raw_value_text"]
        assert r.evidence.source is not None and f"line {n}" in (r.evidence.source.locator or "")


def test_source_metadata_is_complete_and_pinned(release) -> None:  # type: ignore[no-untyped-def]
    assert release.metadata["upstream_commit"] == COMMIT
    assert len(release.metadata["upstream_data_file_sha256"]) == 64
    assert release.license is not None and release.license.identifier == "CC-BY-4.0"
    assert release.license.redistribution_allowed is None  # unknown is never recorded as allowed
    assert release.metadata["uncertainty_in_source"] == "none provided"
    for r in release.records:
        s = r.evidence.source
        assert s is not None and COMMIT in s.url and "Ember" in (s.citation or "") and s.retrieved_on == "2026-10-15"
        assert r.evidence.scope.period == "2024" and r.temporal is not None and r.geography_codes


def test_raw_files_verify_against_their_pins(release) -> None:  # type: ignore[no-untyped-def]
    assert len(release.raw_files) == 2
    assert verify_raw_files(release, RAW) == []


def test_release_is_reproducible_byte_for_byte_from_spec_and_raw_files(release) -> None:  # type: ignore[no-untyped-def]
    spec = load_import_spec(SPEC)
    res = build_release(spec, RAW, model_spec=MODEL)
    stored = (ROOT / "data" / "releases" / DS / VER / "dataset.json").read_text(encoding="utf-8")
    assert res.dataset_json == stored
    stored_report = (ROOT / "data" / "releases" / DS / VER / "import_report.json").read_text(encoding="utf-8")
    assert res.report_json == stored_report
    assert (
        res.report["release_content_sha256"]
        == DatasetRegistry.from_directory(ROOT / "data" / "releases").get(DS, VER).content_sha256
    )


def test_evidence_status_is_honest(release) -> None:  # type: ignore[no-untyped-def]
    for r in release.records:
        assert r.status().value == "sourced_unreviewed"
        assert r.evidence.review is None  # no reviewer was invented
        assert r.evidence.uncertainty.kind == "unknown"  # no uncertainty was invented
        assert not is_evidence_backed(r.evidence)
        issues = " ".join(evidence_backed_issues(r.evidence))
        assert "uncertainty not characterized" in issues and "cited_unreviewed" in issues


def test_unit_converts_to_the_model_canonical_unit_with_the_documented_factor(release) -> None:  # type: ignore[no-untyped-def]
    canon = next(i.canonical_unit for i in MODEL.inputs if i.id == "f")
    assert canon == "kgCO2e/kWh"
    india = next(r for r in release.records if r.evidence.scope.geography == "India")
    assert to_canonical(india.evidence.value, india.evidence.unit, canon) == pytest.approx(0.7054, rel=1e-12)


def test_this_release_supplies_f_and_nothing_else(registry: DatasetRegistry) -> None:
    av = registry.parameter_availability(MODEL).to_dict()
    cands = av["candidates"]
    assert sorted(cands["f"][i]["evidence_id"] for i in range(5)) == sorted(
        f"{DS}@{VER}:f.{g}.2024" for g in ("global", "in", "us", "de", "fr")
    )
    assert all(c["eligible"] for c in cands["f"])
    others = [i.id for i in MODEL.inputs if i.id != "f"]
    assert all(not cands.get(v) for v in others), "another variable became 'available' without real evidence"


def test_no_synthetic_or_illustrative_record_in_the_committed_registry(registry: DatasetRegistry) -> None:
    for lr in registry.releases():
        for rec in lr.release.records:
            ev = rec.evidence
            assert ev.provenance_class.value in {"measured", "sourced"}
            assert ev.source is not None and not ev.source.source_id.startswith("fixture:")
            assert "synthetic" not in lr.release.dataset_id


@pytest.mark.skipif(
    not os.environ.get("ECOBACKFIRE_OWID_CHECKOUT"), reason="set ECOBACKFIRE_OWID_CHECKOUT to verify against upstream"
)
def test_excerpt_lines_are_byte_identical_to_upstream() -> None:  # pragma: no cover - needs a checkout
    up = Path(os.environ["ECOBACKFIRE_OWID_CHECKOUT"])
    for excerpt, upstream in (
        ("owid-energy-data.excerpt.csv", "owid-energy-data.csv"),
        ("owid-energy-codebook.excerpt.csv", "owid-energy-codebook.csv"),
    ):
        ex = (RAW / DS / VER / excerpt).read_bytes().split(b"\n")
        full = set((up / upstream).read_bytes().split(b"\n"))
        assert all(line in full for line in ex if line)
