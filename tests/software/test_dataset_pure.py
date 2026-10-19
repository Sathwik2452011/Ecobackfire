"""Dataset layer, pure parts (status, versions, periods, geography, layout). Stdlib only: no pydantic, no pytest.

All fixtures are SYNTHETIC (temp directories with empty placeholder files). Nothing here is environmental data.
"""

import datetime as dt
import tempfile
from pathlib import Path

from ecobackfire.datasets import geography as geo
from ecobackfire.datasets import layout, temporal, versioning
from ecobackfire.datasets.status import (
    RECORD_STATUSES,
    RESOLUTION_ONLY_STATUSES,
    EvidenceStatus,
    is_resolution_only,
    status_from_provenance,
    status_rank,
    weakest_status,
)


def raises(exc, fn, *a, **k):  # type: ignore[no-untyped-def]
    try:
        fn(*a, **k)
    except exc:
        return True
    raise AssertionError(f"{fn.__name__} did not raise {exc.__name__}")


# ------------------------------------------------------------------ status
def test_status_mapping_all_provenance_classes() -> None:
    S = EvidenceStatus
    assert status_from_provenance("measured", reviewed=False) is S.MEASURED
    assert status_from_provenance("measured", reviewed=True) is S.MEASURED
    assert status_from_provenance("sourced", reviewed=True) is S.VERIFIED_SOURCE
    assert status_from_provenance("sourced", reviewed=False) is S.SOURCED_UNREVIEWED
    assert status_from_provenance("estimated", reviewed=False) is S.DERIVED
    assert status_from_provenance("simulated", reviewed=False) is S.DERIVED
    assert status_from_provenance("user_supplied", reviewed=False) is S.ASSUMPTION
    assert status_from_provenance("illustrative", reviewed=False) is S.ILLUSTRATIVE


def test_unreviewed_citation_is_never_verified() -> None:
    assert status_from_provenance("sourced", reviewed=False) is not EvidenceStatus.VERIFIED_SOURCE


def test_unknown_provenance_fails_loudly() -> None:
    raises(ValueError, status_from_provenance, "ai_generated", reviewed=False)


def test_brief_minimum_vocabulary_present() -> None:
    names = {s.value for s in EvidenceStatus}
    brief = {"verified_source", "measured", "derived", "assumption", "illustrative", "missing", "conflicting"}
    assert brief <= names


def test_resolution_only_statuses_are_not_record_statuses() -> None:
    assert set(RECORD_STATUSES).isdisjoint(RESOLUTION_ONLY_STATUSES)
    assert set(RECORD_STATUSES) | set(RESOLUTION_ONLY_STATUSES) == set(EvidenceStatus)
    for s in RESOLUTION_ONLY_STATUSES:
        assert is_resolution_only(s)
        raises(ValueError, status_rank, s)


def test_weakest_status() -> None:
    S = EvidenceStatus
    assert weakest_status([]) is None
    assert weakest_status([S.MEASURED, S.ILLUSTRATIVE, S.DERIVED]) is S.ILLUSTRATIVE
    assert weakest_status([S.VERIFIED_SOURCE, S.SOURCED_UNREVIEWED]) is S.SOURCED_UNREVIEWED
    assert weakest_status([S.ASSUMPTION, S.DERIVED]) is S.ASSUMPTION
    raises(ValueError, weakest_status, [S.MEASURED, S.MISSING])


# ------------------------------------------------------------------ versions
def test_version_parse_and_order() -> None:
    assert str(versioning.parse_version("1.10.0")) == "1.10.0"
    assert versioning.sort_versions(["1.10.0", "1.9.0", "0.2.0", "1.9.10"]) == ["0.2.0", "1.9.0", "1.9.10", "1.10.0"]
    assert versioning.latest_version(["2.0.0", "10.0.0", "9.9.9"]) == "10.0.0"


def test_version_rejects_malformed() -> None:
    for bad in ["1.0", "v1.0.0", "1.0.0-rc1", "01.0.0", "1.0.0.0", "", "a.b.c", "1.0.0+x", " 1.0.0"]:
        assert not versioning.is_valid_version(bad), bad
        raises(ValueError, versioning.parse_version, bad)
    raises(ValueError, versioning.latest_version, [])
    raises(ValueError, versioning.sort_versions, ["1.0.0", "x"])


# ------------------------------------------------------------------ periods
def test_partial_dates_span_whole_period() -> None:
    p = temporal.make_period("2022", "2022")
    assert (p.start, p.end) == (dt.date(2022, 1, 1), dt.date(2022, 12, 31))
    q = temporal.make_period("2020-02", "2020-02")
    assert (q.start, q.end) == (dt.date(2020, 2, 1), dt.date(2020, 2, 29))  # leap year
    r = temporal.make_period("2021-03-05", "2021-03-09")
    assert (r.start, r.end) == (dt.date(2021, 3, 5), dt.date(2021, 3, 9))


def test_period_rejects_bad_input() -> None:
    bad = [("2022", "2021"), ("2022-13", "2022-13"), ("2022-02-30", "2022-03"), ("22", "2022"), ("2022/01", "2022")]
    for s, e in bad:
        raises(ValueError, temporal.make_period, s, e)
    raises(ValueError, temporal.parse_iso_date, "2022-1-1")
    raises(ValueError, temporal.parse_iso_date, "2022-02-30")
    assert temporal.parse_iso_date("2022-02-28") == dt.date(2022, 2, 28)


def test_overlap_contains_hull() -> None:
    a = temporal.make_period("2018", "2020")
    b = temporal.make_period("2020", "2022")
    c = temporal.make_period("2021", "2022")
    assert temporal.overlaps(a, b) and not temporal.overlaps(a, c)
    assert temporal.contains(b, c) and not temporal.contains(c, b)
    d1 = temporal.make_period("2020-01-01", "2020-01-05")
    assert temporal.overlaps(d1, temporal.make_period("2020-01-05", "2020-01-09"))  # shared day counts
    assert not temporal.overlaps(d1, temporal.make_period("2020-01-06", "2020-01-09"))
    h = temporal.hull([a, c])
    assert h is not None and (h.start, h.end) == (dt.date(2018, 1, 1), dt.date(2022, 12, 31))
    assert temporal.hull([]) is None


# ------------------------------------------------------------------ geography
def test_geography_normalize_and_reject() -> None:
    assert geo.normalize_code(" in ") == "IN"
    assert geo.normalize_code("in-ap") == "IN-AP"
    assert geo.normalize_code("global") == "GLOBAL"
    assert geo.normalize_code("m49:150") == "M49:150"
    for bad in ["", "India", "IND", "I", "IN-", "IN-ABCD", "M49:15", "1N", "IN_AP"]:
        raises(ValueError, geo.normalize_code, bad)
    assert [geo.code_level(c) for c in ["GLOBAL", "M49:150", "IN", "IN-AP"]] == [
        "global",
        "m49",
        "country",
        "subdivision",
    ]


def test_geography_containment_is_directional() -> None:
    assert geo.covers("GLOBAL", "IN-AP") and geo.covers("GLOBAL", "M49:150")
    assert geo.covers("IN", "IN-AP") and geo.covers("IN", "IN")
    assert not geo.covers("IN-AP", "IN")  # a state value does not cover the country
    assert not geo.covers("IN", "PK") and not geo.covers("IN", "PK-SD")
    assert not geo.covers("M49:150", "FR")  # M49 membership is deliberately not resolved
    assert not geo.covers("IN", "GLOBAL")
    assert not geo.covers("IN-AP", "IN-KA")  # sibling subdivisions do not cover each other
    assert geo.covers_any(["PK", "IN"], "IN-AP") and not geo.covers_any([], "IN")


# ------------------------------------------------------------------ layout
def _mk(root: Path, *parts: str, file: bool = True) -> None:
    d = root.joinpath(*parts)
    d.mkdir(parents=True, exist_ok=True)
    if file:
        (d / layout.DATASET_FILENAME).write_text("{}", encoding="utf-8")


def test_dataset_id_rules() -> None:
    for ok in ["a", "grid-ef", "iea.2024_v1", "9lives"]:
        assert layout.valid_dataset_id(ok), ok
    for bad in ["", "-a", "A", "has space", "a/b", ".hidden", "x" * 65, "é"]:
        assert not layout.valid_dataset_id(bad), bad


def test_discover_orders_numerically_and_reports_problems() -> None:
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        _mk(root, "alpha", "1.9.0")
        _mk(root, "alpha", "1.10.0")
        _mk(root, "beta", "0.1.0")
        _mk(root, "alpha", "2.0.0", file=False)  # release dir without dataset.json
        _mk(root, "alpha", "latest")  # not a version
        _mk(root, "Bad Name", "1.0.0")  # not a dataset id
        _mk(root, ".git", "1.0.0")  # hidden: ignored silently
        (root / "README.md").write_text("x", encoding="utf-8")  # plain file: ignored
        found, problems = layout.discover(root)
        got = [(f.dataset_id, f.version) for f in found]
        assert got == [("alpha", "1.9.0"), ("alpha", "1.10.0"), ("beta", "0.1.0")]
        assert len(problems) == 3, problems
        assert any("2.0.0" in p and "missing dataset.json" in p for p in problems)
        assert any("latest" in p for p in problems)
        assert any("Bad Name" in p for p in problems)
        assert all(f.path.name == "dataset.json" for f in found)


def test_discover_missing_root_raises() -> None:
    with tempfile.TemporaryDirectory() as t:
        raises(FileNotFoundError, layout.discover, Path(t) / "nope")


def test_sha256_file_matches_known_vectors_and_detects_change() -> None:
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "f.bin"
        p.write_bytes(b"")
        assert layout.sha256_file(p) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        p.write_bytes(b"abc")
        h = layout.sha256_file(p)
        assert h == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        p.write_bytes(b"abd")
        assert layout.sha256_file(p) != h
        p.write_bytes(b"a" * (3 * (1 << 20) + 7))  # spans several chunks
        import hashlib

        assert layout.sha256_file(p) == hashlib.sha256(b"a" * (3 * (1 << 20) + 7)).hexdigest()
