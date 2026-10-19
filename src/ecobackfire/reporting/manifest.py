"""ReproducibilityManifest assembly and JSON comparison for `reproduce`."""

from __future__ import annotations

import datetime as _dt
import importlib.metadata as md
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from ecobackfire import __version__
from ecobackfire.contracts.resolution import EvidenceResolutionSummary
from ecobackfire.contracts.verification import ReproducibilityManifest
from ecobackfire.core.hashing import canonical_json, sha256_text

DEPENDENCIES = ["pydantic", "pint", "numpy", "scipy"]
TOLERANCE = {"rtol": 1e-9, "atol": 1e-12}


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def code_state(repo_hint: Path) -> tuple[str | None, bool | None]:
    commit = _git(["rev-parse", "HEAD"], repo_hint)
    if commit is None:
        return None, None
    status = _git(["status", "--porcelain", "--untracked-files=no"], repo_hint)
    return commit, (bool(status) if status is not None else None)


def dependency_versions() -> dict[str, str]:
    out: dict[str, str] = {}
    for d in DEPENDENCIES:
        try:
            out[d] = md.version(d)
        except md.PackageNotFoundError:
            out[d] = "not installed"
    return out


def build_manifest(
    *,
    run_id: str,
    config_sha256: str,
    model_ref: str,
    artifact_hashes: dict[str, str],
    source_config_path: str | None,
    reproduce_command: str,
    evidence: EvidenceResolutionSummary | None = None,
) -> ReproducibilityManifest:
    repo = Path(__file__).resolve().parents[3]
    commit, dirty = code_state(repo)
    return ReproducibilityManifest(
        run_id=run_id,
        created_at=_dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        package_version=__version__,
        git_commit=commit,
        git_dirty=dirty,
        python=sys.version.split()[0],
        platform=platform.platform(),
        dependencies=dependency_versions(),
        config_sha256=config_sha256,
        config_file="config.json",
        source_config_path=source_config_path,
        model_ref=model_ref,
        dataset_release_ref=None
        if evidence is None
        else "; ".join(f"{r.dataset_id}@{r.version} content_sha256={r.content_sha256}" for r in evidence.releases_used),
        seed=None,
        artifact_hashes=dict(sorted(artifact_hashes.items())),
        reproduce_command=reproduce_command,
        expected_tolerance=dict(TOLERANCE),
        evidence_resolution_sha256=None
        if evidence is None
        else sha256_text(canonical_json(evidence.model_dump(mode="json"))),
        evidence_registry_sha256=None if evidence is None else evidence.registry_sha256,
    )


def json_close(a: Any, b: Any, rtol: float, atol: float, path: str = "$") -> list[str]:
    """Structural comparison; floats within tolerance. Returns a list of differences."""
    if isinstance(a, dict) and isinstance(b, dict):
        diffs: list[str] = []
        if a.keys() != b.keys():
            return [f"{path}: keys differ"]
        for k in a:
            diffs += json_close(a[k], b[k], rtol, atol, f"{path}.{k}")
        return diffs
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} vs {len(b)}"]
        diffs = []
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            diffs += json_close(x, y, rtol, atol, f"{path}[{i}]")
        return diffs
    if isinstance(a, float) or isinstance(b, float):
        try:
            return [] if abs(float(a) - float(b)) <= atol + rtol * abs(float(b)) else [f"{path}: {a!r} vs {b!r}"]
        except (TypeError, ValueError):
            return [f"{path}: {a!r} vs {b!r}"]
    return [] if a == b else [f"{path}: {a!r} vs {b!r}"]
