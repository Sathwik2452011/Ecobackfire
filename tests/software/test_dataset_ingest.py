"""Tests for the dataset import path (datasets/ingest.py + `ecobackfire datasets`).

EVERYTHING IN THIS FILE IS SYNTHETIC. The CSV rows, numbers, source id ('fixture:not-a-real-source') and country
names are invented to exercise validation. None of it is environmental data and none of it may be copied into a
real dataset. The real, committed release is tested in tests/scientific/test_owid_carbon_intensity_release.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecobackfire.cli.main import main
from ecobackfire.datasets.ingest import (
    build_release,
    load_import_spec,
    parse_decimal,
    verify_raw_files,
    write_release,
)
from ecobackfire.datasets.registry import DatasetRegistry
from ecobackfire.errors import ConfigError, DatasetValidationError
from ecobackfire.models.registry import get_model

MODEL = get_model("efficiency_rebound@1.0.0").spec
CB_UNIT = "synthetic grams per kWh"  # stands in for a codebook unit text

DATA = "name,yr,iso3,syn_f,syn_eta\nSynland,2000,SYN,500.5,0.25\nOtherland,2000,OTH,100.000,0.5\n"
CODEBOOK = (
    f"column,title,description,unit,source\nsyn_f,t,d,{CB_UNIT},synthetic\nsyn_eta,t,d,fraction (synthetic),synthetic\n"
)


def _sha(b: bytes) -> str:
    import hashlib

    return hashlib.sha256(b).hexdigest()


def make_spec(
    tmp: Path,
    *,
    data: str = DATA,
    codebook: str = CODEBOOK,
    pin_data: str | None = None,
    variables: str | None = None,
    selects: str | None = None,
    source_id: str = "fixture:not-a-real-source",
    citation: str = "Synthetic test citation (not real)",
    url: str = "https://example.org/synthetic",
    model: bool = True,
) -> Path:
    raw = tmp / "raw"
    raw.mkdir(exist_ok=True)
    (raw / "d.csv").write_bytes(data.encode())
    (raw / "c.csv").write_bytes(codebook.encode())
    variables = (
        variables
        or f"""
[[variables]]
column = "syn_f"
variable_id = "f"
unit = "gCO2e/kWh"
codebook_unit_text = "{CB_UNIT}"
description = "synthetic intensity"
"""
    )
    selects = (
        selects
        or """
[[select]]
where = { name = "Synland", yr = "2000" }
geography_code = "IN"
geography_label = "Synland"
source_iso3 = "SYN"
[[select]]
where = { name = "Otherland", yr = "2000" }
geography_code = "DE"
geography_label = "Otherland"
source_iso3 = "OTH"
"""
    )
    target = '[target]\nmodel_ref = "efficiency_rebound@1.0.0"\n' if model else ""
    text = f"""
[release]
dataset_id = "synthetic-import-fixture"
release_version = "1.0.0"
title = "SYNTHETIC import fixture (not real data)"
description = "synthetic"
provider = "synthetic provider"
last_updated = "2026-01-01"
changelog = "synthetic"
{target}
[[raw_files]]
key = "data"
source_id = "{source_id}"
path = "d.csv"
sha256 = "{pin_data or _sha(data.encode())}"
[[raw_files]]
key = "codebook"
source_id = "{source_id}"
path = "c.csv"
sha256 = "{_sha(codebook.encode())}"
[source]
source_id = "{source_id}"
citation = "{citation}"
url = "{url}"
retrieved_on = "2026-01-01"
[table]
file = "data"
label_column = "name"
period_column = "yr"
iso3_column = "iso3"
[codebook]
file = "codebook"
key_column = "column"
unit_column = "unit"
{variables}
{selects}
"""
    p = tmp / "spec.toml"
    p.write_text(text, encoding="utf-8")
    return p


def _var(column: str, variable_id: str, unit: str, cb: str = CB_UNIT) -> str:
    return (
        f'[[variables]]\ncolumn="{column}"\nvariable_id="{variable_id}"\nunit="{unit}"\n'
        f'codebook_unit_text="{cb}"\ndescription="d"\n'
    )


def build(tmp: Path, **kw):  # type: ignore[no-untyped-def]
    spec = load_import_spec(make_spec(tmp, **kw))
    return build_release(spec, tmp / "raw", model_spec=MODEL if spec.target_model_ref else None)


# ------------------------------------------------------------------ happy path / no transformation
def test_import_builds_records_with_values_verbatim(tmp_path: Path) -> None:
    res = build(tmp_path)
    recs = {r.record_id: r for r in res.release.records}
    assert set(recs) == {"f.in.2000", "f.de.2000"}
    r = recs["f.in.2000"]
    assert r.evidence.value == 500.5 and r.evidence.unit == "gCO2e/kWh"  # NOT converted to kg
    assert recs["f.de.2000"].evidence.value == 100.0
    assert r.metadata["raw_value_text"] == "500.5" and recs["f.de.2000"].metadata["raw_value_text"] == "100.000"
    assert r.metadata["transformations"].startswith("none")
    assert r.evidence.provenance_class.value == "sourced" and r.evidence.review is None
    assert r.status().value == "sourced_unreviewed"
    assert r.evidence.uncertainty.kind == "unknown"  # never invented
    assert r.geography_codes == ("IN",) and r.temporal.start == r.temporal.end == "2000"


def test_report_lists_every_record_with_raw_hash_and_no_value_transformations(tmp_path: Path) -> None:
    res = build(tmp_path)
    assert res.report["value_transformations"].startswith("none")
    assert [x["raw_line"] for x in res.report["records"]] == [2, 3]
    assert all(len(x["raw_row_sha256"]) == 64 for x in res.report["records"])
    assert res.report["release_content_sha256"] and res.report["raw_files"][0]["sha256"] == _sha(DATA.encode())


# ------------------------------------------------------------------ determinism, immutability
def test_import_is_deterministic_and_write_is_idempotent_and_append_only(tmp_path: Path) -> None:
    a, b = build(tmp_path), build(tmp_path)
    assert a.dataset_json == b.dataset_json and a.report_json == b.report_json
    root = tmp_path / "reg"
    p1, new1 = write_release(a, root)
    p2, new2 = write_release(b, root)
    assert new1 and not new2 and p1 == p2
    # a different release under the same id/version must be refused
    other = tmp_path / "o"
    other.mkdir()
    changed = build(other, data=DATA.replace("500.5", "501.5"))
    with pytest.raises(DatasetValidationError, match="append-only"):
        write_release(changed, root)
    assert p1.read_text(encoding="utf-8") == a.dataset_json  # untouched


def test_written_release_loads_in_registry_and_validates_raw_hashes(tmp_path: Path) -> None:
    res = build(tmp_path)
    write_release(res, tmp_path / "reg")
    reg = DatasetRegistry.from_directory(tmp_path / "reg")
    lr = reg.get("synthetic-import-fixture", "1.0.0")
    assert lr.content_sha256 == res.report["release_content_sha256"]
    assert verify_raw_files(lr.release, tmp_path / "raw") == []
    (tmp_path / "raw" / "d.csv").write_bytes(DATA.replace("500.5", "999.9").encode())
    assert "hash differs" in verify_raw_files(lr.release, tmp_path / "raw")[0]


# ------------------------------------------------------------------ schema / provenance / raw files
def test_raw_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(DatasetValidationError, match="does not match the pinned"):
        build(tmp_path, pin_data="0" * 64)


def test_missing_raw_file_is_rejected(tmp_path: Path) -> None:
    spec = load_import_spec(make_spec(tmp_path))
    (tmp_path / "raw" / "d.csv").unlink()
    with pytest.raises(DatasetValidationError, match="not found"):
        build_release(spec, tmp_path / "raw", model_spec=MODEL)


@pytest.mark.parametrize(
    "kw,msg",
    [
        ({"source_id": "TODO"}, "placeholder"),
        ({"citation": "tbd"}, "placeholder"),
        ({"url": "not a url"}, "url"),
    ],
)
def test_provenance_problems_are_rejected(tmp_path: Path, kw: dict[str, str], msg: str) -> None:
    with pytest.raises(DatasetValidationError, match=msg):
        build(tmp_path, **kw)


def test_unknown_spec_key_is_rejected(tmp_path: Path) -> None:
    p = make_spec(tmp_path)
    p.write_text(p.read_text(encoding="utf-8").replace("[table]", "[table]\nbogus = 1"), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_import_spec(p)


# ------------------------------------------------------------------ unit validation
def test_codebook_unit_text_must_match_declaration(tmp_path: Path) -> None:
    with pytest.raises(DatasetValidationError, match="codebook unit text"):
        build(tmp_path, codebook=CODEBOOK.replace(CB_UNIT, "something else"))


def test_unknown_unit_is_rejected(tmp_path: Path) -> None:
    v = _var("syn_f", "f", "foobarqux/kWh")
    with pytest.raises(DatasetValidationError, match="unit"):
        build(tmp_path, variables=v)


def test_unit_incompatible_with_model_canonical_unit_is_rejected(tmp_path: Path) -> None:
    v = _var("syn_f", "f", "kg")
    with pytest.raises(DatasetValidationError, match="incompatible"):
        build(tmp_path, variables=v)


def test_variable_not_in_model_is_rejected(tmp_path: Path) -> None:
    v = _var("syn_f", "not_a_model_input", "gCO2e/kWh")
    with pytest.raises(DatasetValidationError, match="not an input"):
        build(tmp_path, variables=v)


def test_value_violating_model_physical_bounds_is_rejected(tmp_path: Path) -> None:
    # eta must be in [0, 1); 1.5 is impossible
    v = _var("syn_eta", "eta", "dimensionless", cb="fraction (synthetic)")
    with pytest.raises(DatasetValidationError, match="physical bounds"):
        build(tmp_path, data=DATA.replace("0.25", "1.5"), variables=v)


def test_unit_conversion_is_checked_but_not_applied(tmp_path: Path) -> None:
    res = build(tmp_path)  # gCO2e/kWh -> kgCO2e/kWh is convertible for the bounds check...
    assert res.release.records[0].evidence.value == 500.5  # ...but the stored value is untouched


# ------------------------------------------------------------------ malformed cells / selection
@pytest.mark.parametrize("cell", ["", "NA", "nan", "inf", "1e3", "1,5", "abc", "--1"])
def test_malformed_numeric_cells_are_rejected(tmp_path: Path, cell: str) -> None:
    with pytest.raises(DatasetValidationError, match="rejected"):
        build(tmp_path, data=DATA.replace("500.5", cell if "," not in cell else '"1,5"'))


def test_negative_emission_factor_violates_model_bounds(tmp_path: Path) -> None:
    with pytest.raises(DatasetValidationError, match="physical bounds"):
        build(tmp_path, data=DATA.replace("500.5", "-3.0"))


def test_selection_must_match_exactly_one_row(tmp_path: Path) -> None:
    s = '[[select]]\nwhere = { name = "Nowhere", yr = "2000" }\ngeography_code = "IN"\ngeography_label = "x"\n'
    with pytest.raises(DatasetValidationError, match="matched 0 rows"):
        build(tmp_path, selects=s)
    dup = DATA + "Synland,2000,SYN,1.0,0.1\n"
    with pytest.raises(DatasetValidationError, match="matched 2 rows"):
        build(tmp_path, data=dup)


def test_iso3_cross_check(tmp_path: Path) -> None:
    s = (
        '[[select]]\nwhere = { name = "Synland", yr = "2000" }\ngeography_code = "IN"\n'
        'geography_label = "x"\nsource_iso3 = "WRONG"\n'
    )
    with pytest.raises(DatasetValidationError, match="iso3"):
        build(tmp_path, selects=s)


def test_unknown_column_is_rejected(tmp_path: Path) -> None:
    v = _var("no_such_col", "f", "gCO2e/kWh")
    with pytest.raises(DatasetValidationError, match=r"no column|codebook has 0 rows"):
        build(tmp_path, variables=v)


def test_all_problems_are_reported_together(tmp_path: Path) -> None:
    bad = DATA.replace("500.5", "NA").replace("100.000", "")
    with pytest.raises(DatasetValidationError) as ei:
        build(tmp_path, data=bad)
    text = str(ei.value)
    assert "2 problem(s)" in text and "Synland" in text and "Otherland" in text


def test_spec_naming_a_model_without_supplying_it_is_rejected(tmp_path: Path) -> None:
    spec = load_import_spec(make_spec(tmp_path))
    with pytest.raises(DatasetValidationError, match="no model spec"):
        build_release(spec, tmp_path / "raw")


@pytest.mark.parametrize("t,v", [("471.080", 471.08), ("0", 0.0), ("-2.5", -2.5), (".5", 0.5), ("7.", 7.0)])
def test_parse_decimal_accepts_plain_decimals(t: str, v: float) -> None:
    assert parse_decimal(t) == v


# ------------------------------------------------------------------ CLI (existing architecture: `ecobackfire ...`)
def test_cli_import_list_validate_roundtrip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = make_spec(tmp_path)
    reg, raw = tmp_path / "reg", tmp_path / "raw"
    assert main(["datasets", "--root", str(reg), "--raw-root", str(raw), "import", str(spec)]) == 0
    assert main(["datasets", "--root", str(reg), "--raw-root", str(raw), "list"]) == 0
    assert main(["datasets", "--root", str(reg), "--raw-root", str(raw), "validate"]) == 0
    assert (
        main(["datasets", "--root", str(reg), "--raw-root", str(raw), "validate", "synthetic-import-fixture@1.0.0"])
        == 0
    )
    out = capsys.readouterr().out
    assert "synthetic-import-fixture@1.0.0" in out and "records=2" in out and "raw file hash(es) verified" in out
    (raw / "d.csv").write_bytes(DATA.replace("500.5", "1.0").encode())  # tamper with the raw file
    assert main(["datasets", "--root", str(reg), "--raw-root", str(raw), "validate"]) == 2


def test_cli_failed_import_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = make_spec(tmp_path, data=DATA.replace("500.5", "NA"))
    reg = tmp_path / "reg"
    assert main(["datasets", "--root", str(reg), "--raw-root", str(tmp_path / "raw"), "import", str(spec)]) == 2
    assert not reg.exists() or not any(reg.rglob("*.json"))
    assert "DatasetValidationError" in capsys.readouterr().err


def test_cli_dry_run_writes_nothing(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    reg = tmp_path / "reg"
    assert (
        main(["datasets", "--root", str(reg), "--raw-root", str(tmp_path / "raw"), "import", str(spec), "--dry-run"])
        == 0
    )
    assert not reg.exists()


def test_dataset_json_is_valid_json_with_sorted_keys(tmp_path: Path) -> None:
    res = build(tmp_path)
    d = json.loads(res.dataset_json)
    assert list(d) == sorted(d)


def test_unknown_unit_is_rejected_even_without_a_target_model(tmp_path: Path) -> None:
    # Without a model there is no canonical unit to convert to, so only the unit-string check can catch this.
    v = _var("syn_f", "f", "foobarqux/kWh")
    with pytest.raises(DatasetValidationError, match=r"cannot interpret unit|unknown unit"):
        build(tmp_path, variables=v, model=False)
