"""Vercel entrypoint. Vercel's Python runtime serves the ASGI object named ``app`` from this file.
All logic lives in web/app.py; this file only exposes it.

Routing note: vercel.json sends every path to this function with the ORIGINAL request path preserved
(``routes``). If a platform rewrite instead hands us the function's own path (``/api/index``), the shim
below maps it back to ``/`` so the demo page is still served. It does not touch any other path."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web.app import app as _web_app  # noqa: E402

_FUNCTION_PATHS = {"/api/index", "/api/index.py"}


async def app(scope, receive, send):  # ASGI wrapper around the unchanged FastAPI app
    if scope.get("type") == "http" and scope.get("path") in _FUNCTION_PATHS:
        scope = dict(scope, path="/", raw_path=b"/")
    await _web_app(scope, receive, send)
