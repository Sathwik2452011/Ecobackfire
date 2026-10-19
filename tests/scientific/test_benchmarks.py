import json
from pathlib import Path

from ecobackfire.models.registry import get_model
from ecobackfire.verification.checks import run_benchmarks

BDIR = Path(__file__).resolve().parents[2] / "benchmarks"


def test_all_known_answer_cases_reproduced():
    ran, failures = run_benchmarks(get_model("efficiency_rebound@1.0.0"), BDIR)
    assert failures == []
    assert len(ran) >= 13


def test_fixture_documents_provenance_of_each_expected_value():
    doc = json.loads((BDIR / "rebound" / "cases.json").read_text(encoding="utf-8"))
    assert all(c["provenance"] for c in doc["cases"])
    assert "do not validate the model against reality" in doc["note"]
