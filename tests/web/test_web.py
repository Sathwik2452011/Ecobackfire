"""Minimal tests for the demo web layer (web/app.py). Skipped when fastapi/httpx are not installed."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from ecobackfire.mission.loader import load_mission
from ecobackfire.mission.runner import run_mission
from web import app as webapp

client = TestClient(webapp.app)


@pytest.fixture(scope="module")
def web_result():
    resp = client.post("/api/mission/run")
    assert resp.status_code == 200
    return resp.json()


def test_health_and_index():
    assert client.get("/api/health").json()["status"] == "ok"
    page = client.get("/")
    assert page.status_code == 200
    assert "Run Demo Mission" in page.text


def test_no_endpoint_accepts_input():
    # arbitrary config / path / command endpoints must not exist
    assert client.post("/api/mission/run", json={"mission": "/etc/passwd"}).status_code == 200  # body ignored
    assert client.get("/openapi.json").status_code == 404
    assert client.post("/api/run").status_code in (404, 405)


def test_web_result_matches_core(web_result, tmp_path):
    core = run_mission(
        load_mission(webapp.MISSION_FILE), tmp_path, webapp.REGISTRY_DIR, source_path=webapp.MISSION_SOURCE_PATH
    ).result.model_dump(mode="json")
    web = web_result["result"]
    assert web["status"] == core["status"] == "completed"
    assert web["candidates"] == core["candidates"]
    assert web["classification"] == core["classification"]
    assert web["falsification"] == core["falsification"]
    assert web["robustness"] == core["robustness"]
    assert web["evidence_summary"] == core["evidence_summary"]
    assert web["budget_usage"] == core["budget_usage"]
    assert web["reproducibility"]["check_outcome"] == core["reproducibility"]["check_outcome"] == "reproduced_exactly"
    assert web["artifacts"] == core["artifacts"]


def test_artifacts_listed_and_downloadable(web_result):
    names = {a["name"] for a in web_result["artifacts"]}
    assert {"result.json", "verification.json", "manifest.json", "report.md", "config.json"} <= names
    for name in names:
        r = client.get(f"/api/artifacts/{name}")
        assert r.status_code == 200, name


@pytest.mark.parametrize("bad", ["../pyproject.toml", "..%2Fpyproject.toml", "nope.json", "%2Fetc%2Fpasswd", ".."])
def test_artifact_whitelist(web_result, bad):
    assert client.get(f"/api/artifacts/{bad}").status_code == 404
