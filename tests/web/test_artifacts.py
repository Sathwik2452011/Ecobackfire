"""Artifact view/download behaviour of the web layer.

The engine writes artifacts under /tmp, which a serverless platform does not keep across instances. These tests pin the
contract that replaced reliance on it:

* a completed run returns its artifact contents in the same response (``artifact_contents``);
* the GET endpoints serve from a snapshot taken at run time (in process memory), never from /tmp and never by running
  the engine again;
* anything not in the snapshot is a clear 404 (JSON ``detail``), never a 500;
* names are looked up in a whitelist, so no filesystem path can be reached.

Nothing here claims durability: a fresh ``ArtifactStore`` stands in for "another/cold instance" and must 404.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from _web_fixtures import _secret, client, fake_search, no_search, researched  # noqa: F401
from _web_helpers import body

from web import app as webapp

REQUIRED = ["result.json", "verification.json", "manifest.json", "report.md", "config.json"]

BAD_NAMES = [
    "../pyproject.toml",
    "..%2Fpyproject.toml",
    "%2e%2e%2fpyproject.toml",
    "..\\pyproject.toml",
    "%2Fetc%2Fpasswd",
    "/etc/passwd",
    "..",
    ".",
    "nope.json",
    "result.json%00",
    "result.json/..",
]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    """Fresh output dir, demo cache and artifact store, so tests neither see nor disturb each other."""
    monkeypatch.setattr(webapp, "OUT_ROOT", tmp_path / "out")
    monkeypatch.setattr(webapp, "_cache", {})
    store = webapp.ArtifactStore()
    monkeypatch.setattr(webapp, "_artifacts", store)
    return store


@pytest.fixture()
def demo(client, isolated):  # noqa: F811
    resp = client.post("/api/mission/run")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ------------------------------------------------------------------------------------------------------ demo mission


def test_demo_run_returns_contents_for_every_required_artifact(demo):
    names = {a["name"] for a in demo["artifacts"]}
    assert set(REQUIRED) <= names
    assert set(REQUIRED) <= set(demo["artifact_contents"])
    # every listed artifact has its contents, and the listing's size/hash describe exactly those contents
    for a in demo["artifacts"]:
        text = demo["artifact_contents"][a["name"]]
        assert a["bytes"] == len(text.encode("utf-8")), a["name"]
        assert a["sha256"] == _sha(text), a["name"]
    json.loads(demo["artifact_contents"]["result.json"])  # parses: it is the real engine output


def test_demo_contents_match_engine_hashes(demo):
    """The hashes the ENGINE recorded for its artifacts equal the hashes of the contents we return."""
    for name, recorded in demo["result"]["artifacts"].items():
        if name in demo["artifact_contents"]:
            assert _sha(demo["artifact_contents"][name]) == recorded, name


def test_demo_get_serves_same_bytes_as_response(client, demo):
    for name in REQUIRED:
        r = client.get(f"/api/artifacts/{name}")
        assert r.status_code == 200, name
        assert r.text == demo["artifact_contents"][name], name
        assert name in r.headers["content-disposition"]
    assert client.get("/api/artifacts/result.json").headers["content-type"].startswith("application/json")
    assert client.get("/api/artifacts/report.md").headers["content-type"].startswith("text/plain")


def test_demo_get_survives_loss_of_tmp(client, demo, monkeypatch):
    """The point of the fix: delete the run directory (what a fresh/recycled instance looks like) and GET still works."""
    shutil.rmtree(webapp.OUT_ROOT)
    assert not webapp.OUT_ROOT.exists()
    monkeypatch.setattr(webapp, "run_mission", _boom)  # and it must not re-run the engine to do so
    for name in REQUIRED:
        r = client.get(f"/api/artifacts/{name}")
        assert r.status_code == 200, name
        assert r.text == demo["artifact_contents"][name]


def test_demo_get_without_a_run_here_is_404_and_does_not_run_engine(client, isolated, monkeypatch):
    monkeypatch.setattr(webapp, "run_mission", _boom)
    r = client.get("/api/artifacts/result.json")
    assert r.status_code == 404
    assert "no such artifact" in r.json()["detail"]


@pytest.mark.parametrize("bad", BAD_NAMES)
def test_demo_get_rejects_paths(client, demo, bad):
    assert client.get(f"/api/artifacts/{bad}").status_code == 404


# ----------------------------------------------------------------------------------------------------- reviewed run


@pytest.fixture(scope="module")
def reviewed():
    """One real reviewed run, shared (a few seconds). Returns (client, response json)."""
    import os

    from fastapi.testclient import TestClient
    from _web_helpers import QUESTION, FakeProvider

    import web.research as R

    os.environ["ECOBACKFIRE_SESSION_SECRET"] = "pytest-secret"
    webapp._provider_factory = lambda: FakeProvider()
    try:
        c = TestClient(webapp.app)
        researched_ = c.post("/api/research", json={"question": QUESTION}).json()
        resp = c.post("/api/run-reviewed", json=body(researched_))
    finally:
        webapp._provider_factory = R.get_provider
    assert resp.status_code == 200, resp.text
    return c, resp.json()


def test_reviewed_run_returns_contents_for_every_artifact(reviewed):
    _c, d = reviewed
    listed = {a["name"] for a in d["artifacts"]}
    assert set(REQUIRED) <= listed
    assert "parameter_review.json" in listed
    assert listed == set(d["artifact_contents"])
    for a in d["artifacts"]:
        text = d["artifact_contents"][a["name"]]
        assert a["bytes"] == len(text.encode("utf-8")), a["name"]
        assert a["sha256"] == _sha(text), a["name"]


def test_reviewed_contents_equal_files_the_engine_wrote(reviewed):
    _c, d = reviewed
    run_dir = Path(d["run_dir"])
    if not run_dir.is_dir():  # another test already cleaned /tmp; the equality below is only meaningful while it exists
        pytest.skip("run directory no longer on disk")
    for name, text in d["artifact_contents"].items():
        assert (run_dir / name).read_bytes() == text.encode("utf-8"), name


def test_reviewed_get_serves_snapshot_even_if_tmp_is_gone(reviewed, monkeypatch):
    c, d = reviewed
    shutil.rmtree(d["run_dir"], ignore_errors=True)  # simulate /tmp not being there
    monkeypatch.setattr(webapp, "run_mission", _boom)
    for name in REQUIRED + ["parameter_review.json"]:
        r = c.get(f"/api/runs/{d['run_key']}/artifacts/{name}")
        assert r.status_code == 200, name
        assert r.text == d["artifact_contents"][name], name


def test_reviewed_get_on_an_instance_that_never_ran_it_is_404_not_500(reviewed, monkeypatch):
    c, d = reviewed
    monkeypatch.setattr(webapp, "_artifacts", webapp.ArtifactStore())  # stands in for a different/cold instance
    monkeypatch.setattr(webapp, "run_mission", _boom)
    r = c.get(f"/api/runs/{d['run_key']}/artifacts/result.json")
    assert r.status_code == 404
    assert "no such artifact" in r.json()["detail"]


@pytest.mark.parametrize("bad", BAD_NAMES)
def test_reviewed_get_rejects_paths(reviewed, bad):
    c, d = reviewed
    assert c.get(f"/api/runs/{d['run_key']}/artifacts/{bad}").status_code == 404


@pytest.mark.parametrize("badkey", ["..", "../x", "ZZZZZZZZZZZZ", "0" * 11, "0" * 13, "0" * 12, "%2e%2e", "demo"])
def test_reviewed_get_rejects_bad_or_unknown_keys(reviewed, badkey):
    c, _d = reviewed
    assert c.get(f"/api/runs/{badkey}/artifacts/result.json").status_code == 404


def test_reviewed_response_still_carries_existing_fields(reviewed):
    """Additive change only: the existing result/provenance fields are all still there."""
    _c, d = reviewed
    for k in ("run_key", "run_id", "result", "artifacts", "web_review", "registry_dir", "run_dir"):
        assert k in d
    assert d["result"]["status"] in ("completed", "completed_no_candidates")


# --------------------------------------------------------------------------------------------- store / snapshot units


def test_store_is_bounded_and_drops_oldest():
    s = webapp.ArtifactStore(max_runs=2)
    s.put("a", {"x": b"1"})
    s.put("b", {"x": b"2"})
    s.put("c", {"x": b"3"})
    assert s.get("a", "x") is None
    assert s.get("b", "x") == b"2" and s.get("c", "x") == b"3"
    assert s.get("c", "missing") is None and s.get("nokey", "x") is None


def test_store_put_copies_input():
    s = webapp.ArtifactStore()
    files = {"x": b"1"}
    s.put("k", files)
    files["x"] = b"changed"
    files["y"] = b"new"
    assert s.get("k", "x") == b"1" and s.get("k", "y") is None


def test_snapshot_reads_only_plain_safe_files(tmp_path, monkeypatch):
    (tmp_path / "result.json").write_text("{}", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "inner.json").write_text("{}", encoding="utf-8")  # directory contents are not artifacts
    (tmp_path / "bad name.json").write_text("{}", encoding="utf-8")  # not a whitelisted name
    outside = tmp_path.parent / f"{tmp_path.name}_secret.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        (tmp_path / "link.txt").symlink_to(outside)  # a symlink must never be followed
    except (OSError, NotImplementedError):
        pass
    monkeypatch.setattr(webapp, "MAX_ARTIFACT_BYTES", 10)
    (tmp_path / "huge.json").write_text("x" * 11, encoding="utf-8")
    snap = webapp._snapshot_dir(tmp_path, extra={"added.json": b"{}"})
    assert set(snap) == {"result.json", "added.json"}


def test_snapshot_of_missing_dir_is_empty_not_an_error(tmp_path):
    assert webapp._snapshot_dir(tmp_path / "does-not-exist") == {}
    assert webapp._snapshot_dir(None) == {}


def test_non_utf8_file_is_listed_but_not_embedded():
    assert webapp._contents_of({"a.json": b"{}", "b.bin": b"\xff\xfe\x00"}) == {"a.json": "{}"}


def _boom(*_a, **_k):
    raise AssertionError("the engine must not run to display an artifact")
