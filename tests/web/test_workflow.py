"""Research -> review -> confirm -> existing engine. Real engine runs (a few seconds each)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from _web_fixtures import _secret, client, fake_search, no_search, researched  # noqa: F401
from _web_helpers import QUESTION, FakeProvider, body, decisions

from ecobackfire.datasets.registry import DatasetRegistry
from ecobackfire.mission.runner import reproduce_mission
from web import app as webapp


@pytest.fixture(scope="module")
def run_data():
    """One full run shared by several assertions (same inputs as the UI test)."""
    import os

    from fastapi.testclient import TestClient

    os.environ["ECOBACKFIRE_SESSION_SECRET"] = "pytest-secret"
    webapp._provider_factory = lambda: FakeProvider()
    c = TestClient(webapp.app)
    r = c.post("/api/research", json={"question": QUESTION}).json()
    resp = c.post("/api/run-reviewed", json=body(r))
    assert resp.status_code == 200, resp.text
    import web.research as R

    webapp._provider_factory = R.get_provider
    return c, r, resp.json()


def test_homepage_has_research_box_and_demo_button(client):
    page = client.get("/").text
    assert "Research question" in page and "Run Demo Mission" in page
    assert "Run EcoBackfire →" in page


def test_parameters_needed_come_from_the_model(client):
    ids = [p["id"] for p in client.get("/api/research/parameters").json()["parameters"]]
    assert ids == ["E0", "EM0", "EM1", "L", "epsilon", "eta", "f", "phi"]


def test_research_does_not_run_the_engine(client, fake_search, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("engine must not run during research")

    monkeypatch.setattr(webapp, "run_mission", boom)
    assert client.post("/api/research", json={"question": QUESTION}).status_code == 200


def test_question_validation(client, fake_search):
    assert client.post("/api/research", json={"question": "short"}).status_code == 422
    assert client.post("/api/research", json={"question": "x" * 601}).status_code == 422
    assert client.post("/api/research", json={"question": QUESTION, "extra": 1}).status_code == 422


def test_no_confirm_no_engine(client, researched, monkeypatch):
    monkeypatch.setattr(webapp, "run_mission", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    assert client.post("/api/run-reviewed", json=body(researched, confirm=False)).status_code == 400


def test_tampered_or_forged_report_rejected(client, researched, monkeypatch):
    monkeypatch.setattr(webapp, "run_mission", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    forged = json.loads(json.dumps(researched))
    forged["report"]["parameters"][0]["candidates"] = [{"candidate_id": "E0.web1"}]
    assert client.post("/api/run-reviewed", json=body(forged)).status_code == 409
    assert client.post("/api/run-reviewed", json={**body(researched), "signature": "0" * 64}).status_code == 409


def test_signature_survives_browser_int_float_roundtrip(researched):
    from web.review import verify_report

    text = json.dumps(researched["report"]).replace(".0,", ",").replace(".0}", "}").replace(".0]", "]")  # 10.0 -> 10
    assert verify_report(json.loads(text), researched["signature"])


def test_engine_ran_and_roles_match_review(run_data):
    _, _, d = run_data
    assert d["result"]["status"] == "completed"
    assert d["web_review"]["engine_consistency_mismatches"] == []
    roles = {i["parameter"]: i["engine_role"] for i in d["web_review"]["engine_inputs"]}
    assert (
        roles["E0"] == "assumed"
        and roles["epsilon"] == "evidence"
        and roles["f"] == "evidence"
        and roles["EM0"] == "illustrative"
    )


def test_user_override_reaches_engine_as_assumption_not_evidence(run_data):
    _, _, d = run_data
    res = d["result"]
    a = next(x for x in res["assumptions"] if x["variable_id"] == "E0")
    assert a["value"] == 1200.0 and a["provenance_class"] == "user_supplied" and "USER OVERRIDE" in a["note"]
    assert "E0" not in {i["variable_id"] for i in res["evidence_items"]}
    ov = next(p for p in d["web_review"]["parameters"] if p["parameter"] == "E0")
    assert ov["basis"] == "user_override" and ov["user_override"]["override_value"] == 1200.0


def test_sources_preserved_in_engine_result(run_data):
    _, _, d = run_data
    sel = {i["variable_id"]: i["selected"] for i in d["result"]["evidence_resolution"]["inputs"] if i.get("selected")}
    assert (
        sel["epsilon"]["source"]["url"] == "https://example.org/b" and sel["epsilon"]["status"] == "sourced_unreviewed"
    )
    assert sel["L"]["source"]["url"] == "https://example.gov.in/l"
    assert sel["f"]["evidence_id"].startswith("owid-electricity-carbon-intensity@1.0.0:f.in.2024")
    q = next(p for p in d["web_review"]["parameters"] if p["parameter"] == "L")["sources"][0]["quote"]
    assert "10 years" in q


def test_conflicting_sources_remain_visible_and_reach_engine(run_data):
    _, _, d = run_data
    eps = next(p for p in d["web_review"]["parameters"] if p["parameter"] == "epsilon")
    assert eps["researched_status"] == "conflicting"
    assert eps["sources"][0]["url"] == "https://example.org/b"
    assert [a["source_url"] for a in eps["alternatives"]] == ["https://example.ac.in/a"]
    reg = DatasetRegistry.from_directory(Path(d["registry_dir"]), strict=True)
    # the engine's registry holds BOTH researched epsilon records (the chosen one is pinned, the other is an alternative)
    assert sorted(e.record.record_id for e in reg.entries(variable_id="epsilon")) == ["epsilon_web1", "epsilon_web2"]
    assert "epsilon" in str(d["result"]["evidence_summary"]["evidence_available"])


def test_artifacts_listed_downloadable_and_safe(run_data):
    c, _, d = run_data
    names = {a["name"] for a in d["artifacts"]}
    assert {
        "mission_result.json",
        "mission_manifest.json",
        "report.md",
        "parameter_review.json",
        "mission.json",
    } <= names
    for n in names:
        assert c.get(f"/api/runs/{d['run_key']}/artifacts/{n}").status_code == 200
    for bad in ("%2e%2e%2fmission.json", "..%2fmission.json", "nope.json", "a%2fb", "%2e%2e"):
        assert c.get(f"/api/runs/{d['run_key']}/artifacts/{bad}").status_code == 404
    for badkey in ("%2e%2e", "..%2f..%2fetc", "zzzzzzzzzzzz", "ABCDEF123456", "0" * 12):
        assert c.get(f"/api/runs/{badkey}/artifacts/mission.json").status_code == 404


def test_reproducibility_check_and_cli_style_reproduce(run_data):
    _, _, d = run_data
    assert d["result"]["reproducibility"]["check_outcome"] == "reproduced_exactly"
    out = reproduce_mission(Path(d["run_dir"]) / "mission_manifest.json", Path(d["registry_dir"]))
    assert out.ok and out.exact, out.details


def test_standard_experiment_artifacts_still_written(run_data):
    _, _, d = run_data
    run_dir = Path(d["run_dir"])
    for n in ("config.json", "result.json", "verification.json", "manifest.json"):
        assert (run_dir / n).is_file()


# ----------------------------------------------------------------------------- refusals: nothing invented, nothing run


@pytest.fixture()
def no_engine(monkeypatch):
    monkeypatch.setattr(
        webapp, "run_mission", lambda *a, **k: (_ for _ in ()).throw(AssertionError("engine must not run"))
    )


def _post(client, researched, **over):
    return client.post("/api/run-reviewed", json=body(researched, decisions(**over)))


def test_unavailable_parameter_blocks_run(client, researched, no_engine):
    r = _post(client, researched, eta={"action": "unavailable"})
    assert r.status_code == 422 and "will not invent" in " ".join(r.json()["messages"])


def test_all_illustrative_or_override_is_refused(client, researched, no_engine):
    r = _post(
        client,
        researched,
        epsilon={"action": "override", "value": 1.0},
        f={"action": "override", "value": 0.65},
        L={"action": "illustrative"},
    )
    assert r.status_code == 422 and "at least one sourced" in " ".join(r.json()["messages"]).lower().replace(
        "parameter", ""
    )


def test_f_has_no_illustrative_option(client, researched, no_engine):
    r = _post(client, researched, f={"action": "illustrative"})
    assert r.status_code == 422 and "evidence-only" in " ".join(r.json()["messages"])


@pytest.mark.parametrize("over,frag", [
    ({"eta": {"action": "override", "value": 1.5}}, "physical bounds"),
    ({"eta": {"action": "override", "value": 0.3, "low": 0.4, "high": 0.5}}, "inside its range"),
    ({"eta": {"action": "override", "value": 0.3, "low": 0.2}}, "both ends"),
    ({"eta": {"action": "override"}}, "needs a value"),
    ({"epsilon": {"action": "accept", "candidate_id": "epsilon.web99"}}, "candidate"),
    ({"epsilon": {"action": "accept"}}, "candidate"),
    ({"L": {"action": "accept", "candidate_id": "epsilon.web1"}}, "candidate"),  # another parameter's candidate
])  # fmt: skip
def test_invalid_decisions_refused(client, researched, no_engine, over, frag):
    r = _post(client, researched, **over)
    assert r.status_code == 422 and frag in " ".join(r.json()["messages"])


def test_missing_decision_and_unknown_parameter_refused(client, researched, no_engine):
    d = decisions()[:-1]
    assert client.post("/api/run-reviewed", json=body(researched, d)).status_code == 422
    d = [*decisions(), {"parameter": "s_max", "action": "illustrative"}]
    assert client.post("/api/run-reviewed", json=body(researched, d)).status_code == 422


def test_non_finite_and_extra_fields_rejected(client, researched, no_engine):
    d = decisions()
    d[0]["python"] = "__import__('os').system('id')"
    assert client.post("/api/run-reviewed", json=body(researched, d)).status_code == 422
    raw = json.dumps(body(researched)).replace('"value": 1200.0', '"value": NaN')
    assert (
        client.post("/api/run-reviewed", content=raw, headers={"content-type": "application/json"}).status_code == 422
    )


def test_no_arbitrary_path_or_config_accepted(client, researched, no_engine):
    b = body(researched)
    b["mission_path"] = "/etc/passwd"
    assert client.post("/api/run-reviewed", json=b).status_code == 422


# ----------------------------------------------------------------------------- web research unavailable


def test_without_search_key_ui_data_is_honest(client, no_search):
    st = client.get("/api/web/status").json()
    assert st["web_research_available"] is False and "ECOBACKFIRE_SEARCH_API_KEY" in st["message"]
    rep = client.post("/api/research", json={"question": QUESTION}).json()["report"]
    assert rep["web_research"]["available"] is False
    by = {p["id"]: p for p in rep["parameters"]}
    assert by["epsilon"]["status"] == "not_researched" and by["epsilon"]["candidates"] == []
    assert by["f"]["candidates"][0]["origin"] == "registry"


def test_without_search_key_run_with_registry_f_and_illustrative_rest(client, no_search):
    r = client.post("/api/research", json={"question": QUESTION}).json()
    decs = decisions(epsilon={"action": "illustrative"}, L={"action": "illustrative"}, E0={"action": "illustrative"})
    resp = client.post("/api/run-reviewed", json=body(r, decs))
    assert resp.status_code == 200 and resp.json()["result"]["status"] == "completed"
    assert resp.json()["web_review"]["engine_consistency_mismatches"] == []


def test_override_of_a_researched_value_is_recorded_with_original(client, researched):
    resp = client.post(
        "/api/run-reviewed",
        json=body(researched, decisions(L={"action": "override", "value": 9.0, "low": 8.0, "high": 11.0})),
    )
    assert resp.status_code == 200
    d = resp.json()
    L = next(p for p in d["web_review"]["parameters"] if p["parameter"] == "L")
    assert L["basis"] == "user_override" and L["used_value"] == 9.0 and L["used_range"] == [8.0, 11.0]
    assert "10" in json.dumps(L["user_override"]["researched"])  # the researched 10-year candidate is kept
    mission = json.loads((Path(d["run_dir"]) / "mission.json").read_text())
    assert (
        mission["bindings"]["L"]["provenance_class"] == "user_supplied"
        and "USER OVERRIDE" in mission["bindings"]["L"]["note"]
    )
    assert "L" not in mission["evidence_requirements"]


def test_demo_routes_unchanged(client):
    assert client.post("/api/mission/run").status_code == 200
    assert client.get("/openapi.json").status_code == 404
