"""Helpers shared by the web tests (TEST-ONLY fake search data lives here)."""

from __future__ import annotations

from web import research as R

QUESTION = (
    "Can improving the efficiency of household air conditioners in India increase total electricity-related emissions?"
)


class FakeProvider:
    name = "fake-test-provider"

    def search(self, query: str, max_results: int = 6) -> list[R.SearchHit]:
        q = query.lower()
        if "elasticity" in q:
            return [
                R.SearchHit(
                    "TEST study A",
                    "https://example.ac.in/a",
                    "TESTDATA. The price elasticity of demand was estimated at -1.2 in the sample.",
                ),
                R.SearchHit(
                    "TEST study B",
                    "https://example.org/b",
                    "TESTDATA. A different study reports price elasticity between 1.5 and 1.7 for the same class.",
                ),
            ]
        if "lifetime" in q:
            return [
                R.SearchHit(
                    "TEST L",
                    "https://example.gov.in/l",
                    "TESTDATA. The average lifetime of the device is 10 years in practice.",
                )
            ]
        return []


def decisions(**over):
    base = {
        "epsilon": {"action": "accept", "candidate_id": "epsilon.web2"},
        "f": {"action": "accept", "candidate_id": "f.registry1"},
        "L": {"action": "accept", "candidate_id": "L.web1"},
        "E0": {"action": "override", "value": 1200.0, "note": "my estimate"},
        "EM0": {"action": "illustrative"},
        "EM1": {"action": "illustrative"},
        "eta": {"action": "illustrative"},
        "phi": {"action": "illustrative"},
    }
    base.update(over)
    return [{"parameter": k, **v} for k, v in base.items()]


def body(researched, decs=None, confirm=True):
    return {
        "report": researched["report"],
        "signature": researched["signature"],
        "decisions": decs or decisions(),
        "confirm": confirm,
    }
