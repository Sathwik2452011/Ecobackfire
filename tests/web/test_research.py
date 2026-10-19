"""Research layer: honesty rules, extraction guards, URL safety, provider handling. Fully offline."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from _web_fixtures import _secret, client, fake_search, no_search, researched  # noqa: E402,F401
from _web_helpers import QUESTION, FakeProvider  # noqa: E402

from ecobackfire.datasets.registry import DatasetRegistry  # noqa: E402
from ecobackfire.models.registry import get_model  # noqa: E402
from web import app as webapp  # noqa: E402
from web import research as R  # noqa: E402

MODEL = get_model(R.MODEL_REF)


def _extract(vid: str, text: str, url: str = "https://example.org/x"):
    iv = MODEL.spec.input_by_id(vid)
    return R.extract_from_hit(vid, R.PARAM_SPECS[vid], R.SearchHit("T", url, text), MODEL.spec, iv.canonical_unit)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "ftp://x.org/a", "http://localhost/a",
    "http://127.0.0.1/a", "http://10.0.0.5/a", "https://user:pw@example.org/a", "https://intranet/a", "https://x.local/a",
    "https://example.org/a b", "", None, 5, "https://exa\nmple.org"])  # fmt: skip
def test_unsafe_urls_rejected(url):
    assert R.validate_source_url(url) is None


def test_safe_url_accepted():
    assert R.validate_source_url("https://www.iea.org/reports/x?y=1") == "https://www.iea.org/reports/x?y=1"


def test_unsafe_url_hit_yields_no_candidate():
    assert _extract("L", "The average lifetime is 10 years.", url="file:///etc/passwd") == []


def test_number_is_verbatim_in_quote_and_unit_converted():
    (c,) = _extract("f", "The grid emission factor was 705 gCO2/kWh in 2024.")
    assert c["matched_text"] in c["quote"] and "705" in c["quote"]
    assert c["unit"] == "gCO2e/kWh" and c["value"] == 705.0
    assert c["canonical_low"] == pytest.approx(0.705)


def test_year_is_not_an_elasticity():
    assert _extract("epsilon", "The elasticity in 2019 was studied.") == []


def test_percent_after_elasticity_rejected():
    assert _extract("epsilon", "Elasticity explains 40% of the variance.") == []


def test_range_gets_labelled_derived_midpoint():
    (c,) = _extract("epsilon", "Price elasticity ranges from 0.3 to 0.7 across studies.")
    assert c["kind"] == "range" and c["range"] == [0.3, 0.7] and c["value"] is None
    assert c["base_value"] == pytest.approx(0.5) and "midpoint" in c["base_value_basis"]


def test_physical_bounds_enforced():
    assert _extract("eta", "Energy savings of 120% were claimed.") == []  # eta must be < 1
    assert len(_extract("eta", "The model gives energy savings of 30% versus the baseline.")) == 1


def test_percent_more_efficient_is_not_eta():
    assert _extract("eta", "The new model is 30% more efficient.") == []


def test_intensity_is_not_embodied_mass():
    for intensity in ("Manufacturing emits 2 kgCO2e per kg of product.", "Embodied emissions of 3 kgCO2e/kWh were assumed.",
                      "The footprint is 40 kg CO2e per year of use."):  # fmt: skip
        assert _extract("EM1", intensity) == [], intensity
    (c,) = _extract(
        "EM1", "The embodied emissions are about 900 kg CO2e per device."
    )  # a mass per device is what the model needs
    assert c["unit"] == "kgCO2e" and c["value"] == 900.0


def test_keyword_required_in_same_sentence():
    assert _extract("L", "It runs 10 years. Lifetime is discussed elsewhere.") == []


def _report(provider, registry=None, question=QUESTION):
    return R.research_question(question, provider=provider, unavailable_reason=None if provider else "no key",
                               registry=registry, mission_file=webapp.MISSION_FILE)  # fmt: skip


def test_missing_evidence_never_gets_a_number():
    rep = _report(FakeProvider())
    by = {p["id"]: p for p in rep["parameters"]}
    for vid in ("E0", "EM0", "EM1", "eta", "phi"):
        assert by[vid]["status"] == "missing" and by[vid]["candidates"] == []
        assert by[vid]["message"] == R.EVIDENCE_INSUFFICIENT


def test_illustrative_default_is_never_a_candidate():
    rep = _report(FakeProvider())
    for p in rep["parameters"]:
        if p["illustrative_default"]:
            assert "ILLUSTRATIVE" in p["illustrative_default"]["note"]
            assert all(c.get("evidence_status") != "illustrative" for c in p["candidates"])
            assert all(c["origin"] in ("web", "registry") for c in p["candidates"])


def test_f_has_no_illustrative_default_like_the_demo_mission():
    rep = _report(FakeProvider())
    assert {p["id"]: p["illustrative_default"] for p in rep["parameters"]}["f"] is None


def test_conflict_and_agreement_classification():
    mk = lambda lo, hi: {"kind": "range" if lo != hi else "value", "canonical_low": lo, "canonical_high": hi}  # noqa: E731
    assert R.classify_candidates([])[0] == "missing"
    assert R.classify_candidates([mk(1, 1)])[0] == "sourced"
    assert R.classify_candidates([mk(1, 2)])[0] == "sourced_range"
    assert R.classify_candidates([mk(1.1, 1.3), mk(1.5, 1.7)])[0] == "conflicting"
    assert R.classify_candidates([mk(1.1, 1.3), mk(1.2, 1.4)])[0] == "multiple_sources"
    assert R.classify_candidates([mk(1.00, 1.00), mk(1.05, 1.05)])[0] == "multiple_sources"


def test_conflicting_sources_both_kept():
    rep = _report(FakeProvider())
    eps = {p["id"]: p for p in rep["parameters"]}["epsilon"]
    assert eps["status"] == "conflicting" and len(eps["candidates"]) == 2


def test_registry_candidate_for_f_matches_geography_only():
    reg = DatasetRegistry.from_directory(webapp.REGISTRY_DIR, strict=True)
    f_in = {p["id"]: p for p in _report(FakeProvider(), reg)["parameters"]}["f"]
    assert [c["value"] for c in f_in["candidates"]] == [705.4] and f_in["candidates"][0][
        "evidence_status"
    ] == "sourced_unreviewed"
    f_br = {
        p["id"]: p
        for p in _report(FakeProvider(), reg, "Does improving air conditioner efficiency in Brazil raise emissions?")[
            "parameters"
        ]
    }["f"]
    assert f_br["candidates"] == [] and f_br["status"] == "missing"  # no silent geography fallback


def test_unavailable_search_is_reported_not_faked():
    reg = DatasetRegistry.from_directory(webapp.REGISTRY_DIR, strict=True)
    rep = _report(None, reg)
    assert rep["web_research"]["available"] is False
    by = {p["id"]: p for p in rep["parameters"]}
    assert all(by[v]["status"] == "not_researched" and not by[v]["candidates"] for v in by if v != "f")
    assert [c["origin"] for c in by["f"]["candidates"]] == ["registry"]


def test_provider_failure_marks_search_failed_not_missing():
    class Boom:
        name = "boom"

        def search(self, q, max_results=6):
            raise R.SearchUnavailable("boom failed: HTTPError")

    by = {p["id"]: p for p in _report(Boom())["parameters"]}
    assert by["L"]["status"] == "search_failed" and by["L"]["candidates"] == []


def test_get_provider_requires_key(monkeypatch):
    monkeypatch.delenv(R.SEARCH_KEY_ENV, raising=False)
    with pytest.raises(R.SearchUnavailable, match="ECOBACKFIRE_SEARCH_API_KEY"):
        R.get_provider()
    monkeypatch.setenv(R.SEARCH_KEY_ENV, "k")
    monkeypatch.setenv(R.SEARCH_PROVIDER_ENV, "nope")
    with pytest.raises(R.SearchUnavailable, match="Unknown search provider"):
        R.get_provider()
    monkeypatch.setenv(R.SEARCH_PROVIDER_ENV, "brave")
    assert R.get_provider().name == "brave"


def test_tavily_parsing_and_key_never_leaks():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "results": [{"title": "T", "url": "https://x.org/a", "content": "c", "published_date": "2024-01-01"}]
            },
        )

    hits = R.TavilyProvider("SECRETKEY", client=httpx.Client(transport=httpx.MockTransport(handler))).search("q")
    assert hits == [R.SearchHit("T", "https://x.org/a", "c", "2024-01-01")] and seen["auth"] == "Bearer SECRETKEY"
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(R.SearchUnavailable) as e:
        R.TavilyProvider("SECRETKEY", client=bad).search("q")
    assert "SECRETKEY" not in str(e.value)


def test_brave_parsing():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-subscription-token"] == "K"
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [{"title": "T", "url": "https://x.org/a", "description": "d", "extra_snippets": ["e"]}]
                }
            },
        )

    (h,) = R.BraveProvider("K", client=httpx.Client(transport=httpx.MockTransport(handler))).search("q")
    assert h.snippet == "d e"


def test_parse_question():
    s = R.parse_question(QUESTION)
    assert s["geography_code"] == "IN" and "household air conditioners" in s["subject"] and s["model_fit_ok"]
    assert R.parse_question("What is the best pizza?")["model_fit_ok"] is False
