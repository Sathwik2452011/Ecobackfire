"""TEST-ONLY server: the real app with a fake search provider (synthetic TESTDATA text) for the jsdom UI smoke test.

FAKE_SEARCH=1 ECOBACKFIRE_SESSION_SECRET=x uvicorn serve_fake_provider:app --app-dir tests/web --port 8765
cd <dir with jsdom installed> && node <repo>/tests/web/ui_smoke.js http://127.0.0.1:8765
"""

import os
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
from web import app as W
from web import research as R


class Fake:
    name = "fake-test-provider"

    def search(self, q, max_results=6):
        ql = q.lower()
        if "elasticity" in ql:
            return [
                R.SearchHit(
                    "TEST study A",
                    "https://example.ac.in/a",
                    "TESTDATA. The price elasticity of demand was estimated at -1.2 in the sample.",
                    None,
                ),
                R.SearchHit(
                    "TEST study B",
                    "https://example.org/b",
                    "TESTDATA. A different study reports price elasticity between 1.5 and 1.7 for the same class.",
                    None,
                ),
            ]
        if "lifetime" in ql:
            return [
                R.SearchHit(
                    "TEST L",
                    "https://example.gov.in/l",
                    "TESTDATA. The average lifetime of the device is 10 years in practice.",
                    None,
                )
            ]
        return []


if os.environ.get("FAKE_SEARCH") == "1":
    W._provider_factory = lambda: Fake()
app = W.app
