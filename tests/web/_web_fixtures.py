"""Fixtures for the web tests (imported explicitly: a tests/web/conftest.py would shadow tests/conftest.py,
which other tests import by name). The fake search provider returns TEST-ONLY text (prefixed TESTDATA) to exercise the
plumbing offline; none of it is scientific data and none of it is shipped with the app."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from _web_helpers import QUESTION, FakeProvider
from fastapi.testclient import TestClient

from web import app as webapp
from web import research as R


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("ECOBACKFIRE_SESSION_SECRET", "pytest-secret")


@pytest.fixture()
def fake_search(monkeypatch):
    monkeypatch.setattr(webapp, "_provider_factory", lambda: FakeProvider())


@pytest.fixture()
def no_search(monkeypatch):
    monkeypatch.delenv(R.SEARCH_KEY_ENV, raising=False)
    monkeypatch.setattr(webapp, "_provider_factory", R.get_provider)


@pytest.fixture()
def client():
    return TestClient(webapp.app)


@pytest.fixture()
def researched(client, fake_search):
    r = client.post("/api/research", json={"question": QUESTION})
    assert r.status_code == 200
    return r.json()
