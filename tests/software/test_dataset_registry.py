"""Dataset registry over temp directories of SYNTHETIC releases (see _dataset_fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _dataset_fixtures import evidence, illustrative_evidence, record, release, write_release

from ecobackfire.contracts.experiment import Binding
from ecobackfire.datasets.registry import DatasetRegistry
from ecobackfire.datasets.status import EvidenceStatus
from ecobackfire.errors import DatasetNotFoundError, DatasetValidationError
from ecobackfire.models.registry import get_model

pytestmark = pytest.mark.software


@pytest.fixture
def root(tmp_path: Path) -> Path:
    write_release(tmp_path, release(release_version="1.9.0"))
    newer = release(release_version="1.10.0", parent_release="1.9.0", records=[record(evidence("r1", value=0.5))])
    write_release(tmp_path, newer)
    write_release(
        tmp_path,
        release(
            dataset_id="other-fixture",
            records=[
                record(illustrative_evidence("e1", "epsilon", 0.5)),
                record(evidence("w1", "f", 9.0), quality="withdrawn"),
            ],
        ),
    )
    return tmp_path


def test_catalogue_and_numeric_version_order(root: Path) -> None:
    reg = DatasetRegistry.from_directory(root)
    assert reg.dataset_ids() == ["other-fixture", "synthetic-fixture"]
    assert reg.versions("synthetic-fixture") == ["1.9.0", "1.10.0"]
    assert reg.latest_version("synthetic-fixture") == "1.10.0"
    assert len(reg) == 3
    assert reg.all_variables() == ["epsilon", "f"]


def test_get_requires_explicit_existing_version(root: Path) -> None:
    reg = DatasetRegistry.from_directory(root)
    assert reg.get("synthetic-fixture", "1.9.0").release.release_version == "1.9.0"
    with pytest.raises(DatasetNotFoundError):
        reg.get("synthetic-fixture", "9.9.9")
    with pytest.raises(DatasetNotFoundError):
        reg.versions("nope")


def test_entries_have_qualified_ids_and_statuses(root: Path) -> None:
    reg = DatasetRegistry.from_directory(root)
    ids = {e.evidence_id for e in reg.entries(variable_id="f")}
    assert {"synthetic-fixture@1.9.0:r1", "synthetic-fixture@1.10.0:r1", "other-fixture@1.0.0:w1"} == ids
    assert reg.entry("other-fixture@1.0.0:e1").status is EvidenceStatus.ILLUSTRATIVE
    assert reg.entry("synthetic-fixture@1.9.0:r1").status is EvidenceStatus.SOURCED_UNREVIEWED
    with pytest.raises(DatasetNotFoundError):
        reg.entry("nope@1.0.0:x")


def test_usable_only_excludes_withdrawn(root: Path) -> None:
    reg = DatasetRegistry.from_directory(root)
    assert "other-fixture@1.0.0:w1" not in {e.evidence_id for e in reg.entries(usable_only=True)}
    assert "other-fixture@1.0.0:w1" in {e.evidence_id for e in reg.entries()}


def test_versions_hold_different_values(root: Path) -> None:
    reg = DatasetRegistry.from_directory(root)
    assert reg.entry("synthetic-fixture@1.9.0:r1").record.evidence.value == 0.4
    assert reg.entry("synthetic-fixture@1.10.0:r1").record.evidence.value == 0.5


def test_misfiled_release_is_an_error(tmp_path: Path) -> None:
    p = write_release(tmp_path, release())
    data = json.loads(p.read_text())
    data["release_version"] = "2.0.0"
    p.write_text(json.dumps(data))
    with pytest.raises(DatasetValidationError, match="stored under"):
        DatasetRegistry.from_directory(tmp_path)


def test_strict_aggregates_problems_and_lenient_keeps_good_releases(tmp_path: Path) -> None:
    write_release(tmp_path, release())
    bad = tmp_path / "broken" / "1.0.0"
    bad.mkdir(parents=True)
    (bad / "dataset.json").write_text("{not json")
    (tmp_path / "Bad Name" / "1.0.0").mkdir(parents=True)
    with pytest.raises(DatasetValidationError, match="2 problem"):
        DatasetRegistry.from_directory(tmp_path)
    reg = DatasetRegistry.from_directory(tmp_path, strict=False)
    assert reg.dataset_ids() == ["synthetic-fixture"] and len(reg.load_errors) == 2


def test_missing_root_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(DatasetValidationError, match="not found"):
        DatasetRegistry.from_directory(tmp_path / "nope")


def test_coverage_counts_unstructured_records_instead_of_guessing(tmp_path: Path) -> None:
    write_release(
        tmp_path,
        release(
            records=[
                record(evidence("a"), geography_codes=["IN"], temporal={"start": "2018", "end": "2020"}),
                record(evidence("b")),
            ]
        ),
    )
    reg = DatasetRegistry.from_directory(tmp_path)
    cov = reg.coverage("synthetic-fixture", "1.0.0")
    assert cov["geography_codes"] == ["IN"]
    assert cov["period_hull"] == {"start": "2018-01-01", "end": "2020-12-31"}
    assert cov["n_records_without_geography_codes"] == 1 and cov["n_records_without_temporal_coverage"] == 1
    a, b = reg.entries()
    assert reg.covers_geography(a, "IN-AP") is True and reg.covers_geography(a, "PK") is False
    assert reg.covers_geography(b, "IN") is None


def test_sources_list_supported_variables(tmp_path: Path) -> None:
    recs = [record(evidence("a", "f")), record(evidence("b", "eta", 0.3, "dimensionless"))]
    write_release(tmp_path, release(records=recs))
    srcs = DatasetRegistry.from_directory(tmp_path).sources("synthetic-fixture", "1.0.0")
    assert len(srcs) == 1 and srcs[0]["variables"] == ["eta", "f"]


def test_hashes_are_stable_and_detect_changes(tmp_path: Path) -> None:
    p = write_release(tmp_path, release(), indent=2)
    r1 = DatasetRegistry.from_directory(tmp_path)
    r1b = DatasetRegistry.from_directory(tmp_path)
    lr = r1.get("synthetic-fixture", "1.0.0")
    assert lr.file_sha256 == r1b.get("synthetic-fixture", "1.0.0").file_sha256 and r1.sha256() == r1b.sha256()
    p.write_text(json.dumps(json.loads(p.read_text()), indent=4))  # formatting only
    lr2 = DatasetRegistry.from_directory(tmp_path).get("synthetic-fixture", "1.0.0")
    assert lr2.file_sha256 != lr.file_sha256 and lr2.content_sha256 == lr.content_sha256
    data = json.loads(p.read_text())
    data["records"][0]["evidence"]["value"] = 0.41  # a value change must change the content hash
    p.write_text(json.dumps(data))
    lr3 = DatasetRegistry.from_directory(tmp_path).get("synthetic-fixture", "1.0.0")
    assert lr3.content_sha256 != lr.content_sha256


def test_index_and_describe_are_json_serialisable(root: Path) -> None:
    reg = DatasetRegistry.from_directory(root)
    json.dumps(reg.index())
    d = reg.describe("synthetic-fixture", "1.9.0")
    json.dumps(d)
    row = d["entries"][0]
    assert row["uncertainty"] == {"kind": "unknown"} and row["status"] == "sourced_unreviewed"
    assert row["source"]["source_id"] == "fixture:not-a-real-source"


def test_parameter_availability_against_real_model_spec(tmp_path: Path) -> None:
    write_release(
        tmp_path,
        release(
            records=[
                record(evidence("ok", "f", 0.4, "kgCO2e/kWh")),
                record(evidence("conv", "f", 400.0, "gCO2e/kWh")),
                record(evidence("bad-unit", "f", 1.0, "kg")),
                record(illustrative_evidence("e", "epsilon", 0.5), quality="withdrawn"),
            ],
            missing_variables=[{"variable_id": "phi", "reason": "synthetic: no value documented"}],
        ),
    )
    spec = get_model("efficiency_rebound@1.0.0").spec
    av = DatasetRegistry.from_directory(tmp_path).parameter_availability(spec)
    by_id = {c.evidence_id.split(":")[1]: c for c in av.candidates["f"]}
    assert by_id["ok"].eligible and by_id["conv"].eligible
    assert not by_id["bad-unit"].eligible and "unit" in by_id["bad-unit"].reasons[0]
    eps = av.candidates["epsilon"][0]
    assert not eps.eligible and "withdrawn" in eps.reasons[0]
    assert "eta" in av.unprovided and "phi" in av.unprovided and "f" not in av.unprovided
    assert "phi" in av.documented_missing
    json.dumps(av.to_dict())


def test_evidence_store_bridge_works_with_existing_binding_check(tmp_path: Path) -> None:
    write_release(tmp_path, release())
    store = DatasetRegistry.from_directory(tmp_path).evidence_store()
    qid = "synthetic-fixture@1.0.0:r1"
    assert store.ids() == [qid]
    b = Binding(value=0.4, unit="kgCO2e/kWh", provenance_class="sourced", evidence_record_id=qid)
    codes = [i.code for i in store.check_binding("f", b, "kgCO2e/kWh")]
    assert codes == ["W_UNREVIEWED"]  # consistent with the record; only the unreviewed-citation warning
    wrong = Binding(value=0.9, unit="kgCO2e/kWh", provenance_class="sourced", evidence_record_id=qid)
    assert "E_VALUE_MISMATCH" in [i.code for i in store.check_binding("f", wrong, "kgCO2e/kWh")]
