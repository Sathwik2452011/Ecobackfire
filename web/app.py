"""Thin, read-only web layer around the existing EcoBackfire mission engine.

Design rules (see WEB_DEMO.md):
  * It calls ``ecobackfire.mission.runner.run_mission`` directly. No pipeline logic lives here.
  * It runs ONLY the canonical mission ``examples/missions/example.toml``. There is no endpoint that accepts a
    config, a path, code or a command.
  * The engine writes its artifacts to a temp directory (Vercel's filesystem is read-only except /tmp). Right after a
    run completes, the produced files are read ONCE and returned inside the run response (``artifact_contents``), so the
    browser can view/download them without another request. They are also kept in a small bounded in-memory store of the
    process that ran the mission, which backs the run-scoped GET endpoints. Neither is durable storage: /tmp and process
    memory do not survive a cold start or a different instance, and a GET that lands on one returns a clear 404.
  * Artifact GET endpoints never run the engine and never read a caller-built path: they look a name up in the snapshot
    taken from the completed run (whitelist = the files the engine actually produced).

Research workflow (added; the demo routes above are unchanged):
  POST /api/research      question -> required parameters -> candidate values/sources (web/research.py). Runs NO engine.
  POST /api/run-reviewed  user-reviewed parameters + explicit confirmation -> web/review.py builds the mission and the
                          run-scoped evidence registry -> the SAME ``run_mission`` call as the demo. No calculation here.
  The request never carries code, a config path or a file path: only the signed research report, per-parameter decisions
  (accept a researched candidate / numeric override / illustrative default) and ``confirm: true``.
"""

from __future__ import annotations

import datetime as _dt
import re
import shutil
import sys
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
_SRC = ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:  # allow running without `pip install -e .` (e.g. Vercel)
    sys.path.insert(0, str(_SRC))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, Response  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field  # noqa: E402

from ecobackfire.core.hashing import pretty_json, sha256_bytes  # noqa: E402
from ecobackfire.datasets.registry import DatasetRegistry  # noqa: E402
from ecobackfire.errors import EcoBackfireError  # noqa: E402
from ecobackfire.mission.loader import load_mission  # noqa: E402
from ecobackfire.mission.runner import MissionRun, run_mission  # noqa: E402
from web import research as _research  # noqa: E402
from web import review as _review  # noqa: E402

MISSION_FILE = ROOT / "examples" / "missions" / "example.toml"
MISSION_SOURCE_PATH = "examples/missions/example.toml"  # recorded in artifacts exactly as the CLI would
REGISTRY_DIR = ROOT / "data" / "releases"
OUT_ROOT = Path(tempfile.gettempdir()) / "ecobackfire_web_runs"
INDEX_HTML = ROOT / "web" / "static" / "index.html"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.\-]+$")

app = FastAPI(title="EcoBackfire demo", docs_url=None, redoc_url=None, openapi_url=None)

_lock = threading.Lock()
_cache: dict[str, MissionRun] = {}


# ------------------------------------------------------------------------------------- artifact snapshots
MAX_ARTIFACT_BYTES = 2_000_000  # per file; larger files are listed but not snapshotted
MAX_STORED_RUNS = 16  # bounded: oldest snapshot is dropped first
DEMO_KEY = "demo"  # store key of the canonical demo mission (reviewed runs use their 12-hex run key)


class ArtifactStore:
    """Bounded, thread-safe, in-memory map ``run key -> {artifact name -> bytes}``.

    It holds a copy of what a completed run produced, so serving an artifact never depends on /tmp still existing.
    It is per-process and NOT durable: a cold start or another serverless instance starts empty.
    """

    def __init__(self, max_runs: int = MAX_STORED_RUNS) -> None:
        self._max = max_runs
        self._runs: OrderedDict[str, dict[str, bytes]] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, key: str, files: dict[str, bytes]) -> None:
        with self._lock:
            self._runs[key] = dict(files)
            self._runs.move_to_end(key)
            while len(self._runs) > self._max:
                self._runs.popitem(last=False)

    def get(self, key: str, name: str) -> bytes | None:
        with self._lock:
            files = self._runs.get(key)
            return None if files is None else files.get(name)

    def clear(self) -> None:
        with self._lock:
            self._runs.clear()


_artifacts = ArtifactStore()


def _snapshot_dir(run_dir: Path | None, extra: dict[str, bytes] | None = None) -> dict[str, bytes]:
    """Read the files the engine wrote for a run (regular files only, no symlinks, size-capped). Never raises."""
    files: dict[str, bytes] = {}
    if run_dir is not None:
        try:
            for p in sorted(run_dir.iterdir()):
                try:
                    if p.is_symlink() or not p.is_file() or not _SAFE_NAME.match(p.name):
                        continue
                    if p.stat().st_size > MAX_ARTIFACT_BYTES:
                        continue
                    files[p.name] = p.read_bytes()
                except OSError:
                    continue
        except OSError:
            pass
    files.update(extra or {})
    return files


def _contents_of(files: dict[str, bytes]) -> dict[str, str]:
    """Text view of a snapshot for the JSON response (all engine artifacts are UTF-8 JSON/Markdown)."""
    out: dict[str, str] = {}
    for name, data in files.items():
        try:
            out[name] = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return out


def _artifact_response(key: str, name: str) -> Response:
    """Serve one artifact from the run's snapshot. 404 (never 500) for anything not in it."""
    data = _artifacts.get(key, name) if _SAFE_NAME.match(name) else None
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="no such artifact (artifacts are held by the server instance that ran the mission and are not "
            "persistent; use the copy returned with the run, or run it again)",
        )
    media = "application/json" if name.endswith(".json") else "text/plain; charset=utf-8"
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff"},
    )


def execute_canonical_mission() -> MissionRun:
    """Run (or return the cached) canonical mission through the existing engine."""
    with _lock:
        run = _cache.get("canonical")
        if run is not None and run.run_dir is not None and run.run_dir.is_dir():
            return run
        mission = load_mission(MISSION_FILE)
        run = run_mission(mission, OUT_ROOT, REGISTRY_DIR, source_path=MISSION_SOURCE_PATH)
        _cache["canonical"] = run
        return run


def _artifact_list(run: MissionRun, files: dict[str, bytes]) -> list[dict[str, Any]]:
    """Name / size / sha256 of each file in the run's snapshot (same shape as before)."""
    hashes = run.result.artifacts
    return [
        {"name": name, "bytes": len(data), "sha256": hashes.get(name) or sha256_bytes(data)}
        for name, data in sorted(files.items())
    ]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mission": MISSION_SOURCE_PATH}


@app.post("/api/mission/run")
def run_canonical() -> dict[str, Any]:
    """Run the canonical mission. Takes no input by design.

    The response carries the artifact contents (``artifact_contents``) read from the completed run, so viewing or
    downloading them does not depend on a later request reaching this same instance.
    """
    run = execute_canonical_mission()
    files = _snapshot_dir(run.run_dir)
    _artifacts.put(DEMO_KEY, files)
    return {
        "run_id": run.run_dir.name if run.run_dir else None,
        "result": run.result.model_dump(mode="json"),
        "artifacts": _artifact_list(run, files),
        "artifact_contents": _contents_of(files),
    }


@app.get("/api/artifacts/{name}")
def artifact(name: str) -> Response:
    """Serve an artifact of the demo run held by THIS process. Never runs the engine; 404 if not held here."""
    return _artifact_response(DEMO_KEY, name)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))


# ====================================================================================== research workflow (new)

REVIEWED_ROOT = OUT_ROOT / "reviewed"
_KEY = re.compile(r"^[0-9a-f]{12}$")
MAX_BODY_BYTES = 400_000
_run_lock = threading.Lock()  # one engine run at a time per process (bounded CPU)
_registry_cache: dict[str, DatasetRegistry] = {}


def _provider_factory() -> _research.SearchProvider:  # indirection so tests can inject a provider
    return _research.get_provider()


def _local_registry() -> DatasetRegistry | None:
    if "r" not in _registry_cache:
        try:
            _registry_cache["r"] = DatasetRegistry.from_directory(REGISTRY_DIR, strict=True)
        except EcoBackfireError:
            return None
    return _registry_cache["r"]


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=600)


class ReviewedRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report: dict[str, Any]
    signature: str = Field(max_length=128)
    decisions: list[_review.Decision] = Field(max_length=16)
    confirm: bool = False


@app.get("/api/web/status")
def web_status() -> dict[str, Any]:
    try:
        prov = _provider_factory()
        return {
            "web_research_available": True,
            "provider": prov.name,
            "message": f"Web search via {prov.name} is configured.",
        }
    except _research.SearchUnavailable as exc:
        return {"web_research_available": False, "provider": None, "message": str(exc)}


@app.get("/api/research/parameters")
def research_parameters() -> dict[str, Any]:
    """The model inputs the engine needs (shown to the user while research is running)."""
    return {"model_ref": _research.MODEL_REF, "parameters": _research.required_parameters()}


@app.post("/api/research")
def research(req: ResearchRequest) -> dict[str, Any]:
    """question -> parameters the engine needs -> candidate values + sources. Does NOT run the engine."""
    question = re.sub(r"[\x00-\x1f\x7f]+", " ", req.question).strip()
    question = re.sub(r"\s+", " ", question)
    if len(question) < 10:
        raise HTTPException(status_code=422, detail="Please enter a research question (at least 10 characters).")
    try:
        provider: _research.SearchProvider | None = _provider_factory()
        reason = None
    except _research.SearchUnavailable as exc:
        provider, reason = None, str(exc)
    report = _research.research_question(
        question, provider=provider, unavailable_reason=reason, registry=_local_registry(), mission_file=MISSION_FILE
    )
    return {"report": report, "signature": _review.sign_report(report)}


@app.post("/api/run-reviewed")
def run_reviewed(req: ReviewedRunRequest) -> JSONResponse:
    """Run the EXISTING engine on the parameters the user reviewed. Requires ``confirm: true`` and a report the server signed."""
    if not req.confirm:
        raise HTTPException(
            status_code=400, detail="Not confirmed: the engine runs only after you confirm the parameters."
        )
    if not _review.verify_report(req.report, req.signature):
        raise HTTPException(
            status_code=409,
            detail="The research session could not be verified (the server restarted, a different server instance "
            "handled it, or the report was altered). Please run the research again. On multi-instance hosts set "
            f"{_review.SECRET_ENV}.",
        )
    today = _dt.datetime.now(_dt.UTC).date().isoformat()
    try:
        built = _review.build_run(
            req.report,
            req.decisions,
            mission_file=MISSION_FILE,
            releases_dir=REGISTRY_DIR,
            work_root=REVIEWED_ROOT,
            today=today,
        )
    except _review.RunBlocked as exc:
        return JSONResponse(status_code=422, content={"blocked": True, "messages": exc.messages})
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=422, detail="The research report is malformed.") from None

    registry = DatasetRegistry.from_directory(built.registry_dir, strict=True)
    runs_root = REVIEWED_ROOT / built.key / "runs"
    with _run_lock:
        shutil.rmtree(runs_root, ignore_errors=True)  # the key is content-derived: a re-run replaces, never mixes
        run = run_mission(built.mission, runs_root, registry, source_path=None)
    assert run.run_dir is not None

    # what the engine itself says about each input (cross-check of the web layer's account)
    roles = {i.variable_id: i for i in (run.result.plan.inputs if run.result.plan else [])}
    expect = {
        "web_sourced": "evidence",
        "registry_sourced": "evidence",
        "user_override": "assumed",
        "illustrative_default": "illustrative",
    }
    engine_inputs, mismatches = [], []
    for o in built.outcomes:
        r = roles.get(o.parameter)
        engine_inputs.append(
            {"parameter": o.parameter, "engine_role": r.role if r else None, "engine_detail": r.detail if r else None}
        )
        if run.result.status in ("completed", "completed_no_candidates") and (r is None or r.role != expect[o.basis]):
            mismatches.append(f"{o.parameter}: web layer says {o.basis}, engine says {r.role if r else 'nothing'}")

    review = dict(built.review_doc)
    review["engine_inputs"] = engine_inputs
    review["engine_consistency_mismatches"] = mismatches
    review["engine_mission_sha256"] = run.result.reproducibility.mission_sha256
    review["reproduce_command"] = (
        f"ecobackfire mission-reproduce <run_dir>/mission_manifest.json --registry <run-scoped registry: {built.registry_dir.name}>"
    )
    text = pretty_json(review)
    (run.run_dir / "parameter_review.json").write_text(text, encoding="utf-8")
    # snapshot AFTER parameter_review.json is written, so every artifact (including it) is returned and served from memory
    files = _snapshot_dir(run.run_dir, extra={"parameter_review.json": text.encode("utf-8")})
    _artifacts.put(built.key, files)
    hashes = dict(run.result.artifacts)
    hashes["parameter_review.json"] = sha256_bytes(text.encode("utf-8"))
    listing = [
        {"name": n, "bytes": len(d), "sha256": hashes.get(n) or sha256_bytes(d)} for n, d in sorted(files.items())
    ]
    return JSONResponse(
        {
            "run_key": built.key,
            "run_id": run.run_dir.name,
            "result": run.result.model_dump(mode="json"),
            "artifacts": listing,
            "artifact_contents": _contents_of(files),
            "web_review": review,
            "registry_dir": str(built.registry_dir),
            "run_dir": str(run.run_dir),
        }
    )


@app.get("/api/runs/{key}/artifacts/{name}")
def reviewed_artifact(key: str, name: str) -> Response:
    """Serve an artifact of a reviewed run held by THIS process (run-scoped by its content-derived key). 404 if not held."""
    if not _KEY.match(key):
        raise HTTPException(status_code=404, detail="no such artifact")
    return _artifact_response(key, name)


@app.exception_handler(RequestValidationError)
def _validation_error(_req: Any, exc: RequestValidationError) -> JSONResponse:
    """422 with location + message only: never echo the offending input (it can be NaN, huge, or hostile)."""
    errs = [
        {"loc": [str(x) for x in e.get("loc", ())], "msg": str(e.get("msg", "invalid"))[:200]}
        for e in exc.errors()[:10]
    ]
    return JSONResponse(status_code=422, content={"detail": errs})
