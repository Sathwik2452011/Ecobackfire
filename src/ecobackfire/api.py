"""Small stable public facade (layer 7). The CLI and any future UI are clients of this module."""

from __future__ import annotations

import json
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ecobackfire.contracts.discovery import DiscoverySpec
from ecobackfire.contracts.evidence_sensitivity import EvidenceSensitivityManifest
from ecobackfire.contracts.experiment import ExperimentConfiguration, ExperimentResult
from ecobackfire.contracts.verification import ReproducibilityManifest, VerificationReport
from ecobackfire.core.hashing import pretty_json, sha256_bytes
from ecobackfire.datasets.registry import DatasetRegistry
from ecobackfire.discovery.evidence_discovery import EvidenceDiscoveryOutcome, evaluate_discovery_with_evidence
from ecobackfire.errors import ConfigError, EcoBackfireError
from ecobackfire.experiments.evidence import materialize_config
from ecobackfire.experiments.runner import config_sha256, run_experiment
from ecobackfire.reporting.evidence_manifest import build_evidence_manifest
from ecobackfire.reporting.evidence_sensitivity_report import render_evidence_sensitivity_report
from ecobackfire.reporting.manifest import TOLERANCE, build_manifest, json_close
from ecobackfire.reporting.markdown import render_report
from ecobackfire.verification.suite import verify as _verify

ARTIFACTS = ["config.json", "result.json", "verification.json", "report.md"]


def load_config(path: str | Path) -> ExperimentConfiguration:
    p = Path(path)
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {p}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {p}: {exc}") from exc
    try:
        return ExperimentConfiguration.model_validate(data)
    except ValidationError as exc:
        if "mission_id" in data and "experiment_id" not in data:
            raise ConfigError(
                f"{p} is a Research Mission, not an experiment configuration.\nUse:\n\n  ecobackfire mission {p}"
            ) from exc
        raise ConfigError(f"invalid configuration {p}:\n{exc}") from exc


RegistryLike = DatasetRegistry | str | Path | None


def _registry_for(config: ExperimentConfiguration, registry: RegistryLike) -> DatasetRegistry | None:
    """Load the dataset registry only when the configuration asks for evidence (strict: any malformed release is
    an error, never a silently skipped dataset)."""
    if not config.evidence_requests:
        return registry if isinstance(registry, DatasetRegistry) else None
    if registry is None:
        raise ConfigError(
            f"this configuration requests evidence for {sorted(config.evidence_requests)}; supply a dataset "
            "registry (CLI: --registry DIR, API: registry=...)"
        )
    if isinstance(registry, DatasetRegistry):
        return registry
    return DatasetRegistry.from_directory(registry, strict=True)


def run_id_for(config: ExperimentConfiguration) -> str:
    return f"{config.experiment_id}-{config_sha256(config)[:8]}"


def _write(path: Path, text: str) -> str:
    data = text.encode("utf-8")
    path.write_bytes(data)
    return sha256_bytes(data)


def _dump(model: Any) -> str:
    return pretty_json(model.model_dump(mode="json"))


def _reproduce_cmd(source_config_path: str | None) -> str:
    return f"ecobackfire run {source_config_path}" if source_config_path else "ecobackfire run <config.toml>"


def canonical_form(model: Any) -> Any:
    """The object exactly as it exists after the JSON round trip every artifact goes through.

    Artifacts are key-sorted JSON; ``verify`` reloads them. Rendering the report from this canonical form (not
    from whatever insertion order the in-memory object happened to have) makes ``report.md`` a pure function of
    the serialized artifacts, so ``run``, ``verify`` and ``reproduce`` produce the same bytes.
    """
    return type(model).model_validate_json(_dump(model))


def write_artifacts(
    run_dir: Path,
    config: ExperimentConfiguration,
    result: ExperimentResult,
    verification: VerificationReport | None,
    source_config_path: str | None,
    reproduce_command: str | None = None,
) -> ReproducibilityManifest:
    run_dir.mkdir(parents=True, exist_ok=True)
    config = canonical_form(config)
    result = canonical_form(result)
    verification = None if verification is None else canonical_form(verification)
    cmd = reproduce_command or _reproduce_cmd(source_config_path)
    hashes = {
        "config.json": _write(run_dir / "config.json", _dump(config)),
        "result.json": _write(run_dir / "result.json", _dump(result)),
    }
    if verification is not None:
        hashes["verification.json"] = _write(run_dir / "verification.json", _dump(verification))
    elif (run_dir / "verification.json").exists():
        (run_dir / "verification.json").unlink()
    hashes["report.md"] = _write(run_dir / "report.md", render_report(config, result, verification, cmd))
    manifest = build_manifest(
        evidence=result.evidence_resolution,
        run_id=run_dir.name,
        config_sha256=result.config_sha256,
        model_ref=result.model_ref,
        artifact_hashes=hashes,
        source_config_path=source_config_path,
        reproduce_command=cmd,
    )
    (run_dir / "manifest.json").write_text(_dump(manifest), encoding="utf-8")
    return manifest


def execute(
    config: ExperimentConfiguration,
    out_root: str | Path = "runs",
    *,
    verify: bool = True,
    source_config_path: str | None = None,
    registry: RegistryLike = None,
    reproduce_command: str | None = None,
) -> Path:
    """Run (and by default verify) an experiment; write all artifacts; return the run directory.

    With ``evidence_requests`` the requests are resolved against ``registry`` first; the artifacts record the
    EFFECTIVE configuration (requests + resolved bindings), so config.json alone identifies every input."""
    reg = _registry_for(config, registry)
    effective, mat = materialize_config(config, reg)
    result = run_experiment(effective, registry=reg)
    verification = _verify(effective, result, verified_evidence=mat.verified_evidence) if verify else None
    run_dir = Path(out_root) / run_id_for(effective)
    write_artifacts(run_dir, effective, result, verification, source_config_path, reproduce_command)
    return run_dir


def reverify(run_dir: str | Path, registry: RegistryLike = None) -> Path:
    """Re-run verification on an existing run directory and rewrite verification/report/manifest."""
    rd = Path(run_dir)
    config = ExperimentConfiguration.model_validate_json((rd / "config.json").read_text(encoding="utf-8"))
    result = ExperimentResult.model_validate_json((rd / "result.json").read_text(encoding="utf-8"))
    old = ReproducibilityManifest.model_validate_json((rd / "manifest.json").read_text(encoding="utf-8"))
    effective, mat = materialize_config(config, _registry_for(config, registry))
    report = _verify(effective, result, verified_evidence=mat.verified_evidence)
    write_artifacts(rd, effective, result, report, old.source_config_path, old.reproduce_command)
    return rd


@dataclass
class ReproduceOutcome:
    ok: bool
    exact: bool
    details: list[str] = field(default_factory=list)


def reproduce(manifest_path: str | Path, registry: RegistryLike = None) -> ReproduceOutcome:
    mp = Path(manifest_path)
    manifest = ReproducibilityManifest.model_validate_json(mp.read_text(encoding="utf-8"))
    run_dir = mp.parent
    config = ExperimentConfiguration.model_validate_json((run_dir / manifest.config_file).read_text(encoding="utf-8"))
    details: list[str] = []
    if config_sha256(config) != manifest.config_sha256:
        return ReproduceOutcome(False, False, ["config.json does not match the manifest's config hash"])
    # Integrity first: the stored artifacts must still be the ones the manifest describes.
    tampered = [
        name
        for name, want in manifest.artifact_hashes.items()
        if not (run_dir / name).exists() or sha256_bytes((run_dir / name).read_bytes()) != want
    ]
    if tampered:
        return ReproduceOutcome(
            False,
            False,
            [f"{n}: stored artifact is missing or modified since the manifest was written" for n in tampered],
        )
    with tempfile.TemporaryDirectory() as tmp:
        try:
            new_dir = execute(
                config,
                tmp,
                verify="verification.json" in manifest.artifact_hashes,
                source_config_path=manifest.source_config_path,
                registry=registry,
                reproduce_command=manifest.reproduce_command,
            )
        except EcoBackfireError as exc:
            if not config.evidence_requests:
                raise
            return ReproduceOutcome(
                False,
                False,
                [
                    "the stored evidence-backed configuration can no longer be re-resolved against the supplied "
                    f"registry ({type(exc).__name__}): {exc}"
                ],
            )
        exact = True
        ok = True
        for name, want in manifest.artifact_hashes.items():
            got_bytes = (new_dir / name).read_bytes()
            if sha256_bytes(got_bytes) == want:
                continue
            exact = False
            old_bytes = (run_dir / name).read_bytes()
            if name.endswith(".json"):
                diffs = json_close(json.loads(old_bytes), json.loads(got_bytes), TOLERANCE["rtol"], TOLERANCE["atol"])
                if diffs:
                    ok = False
                    details.append(f"{name}: differs beyond tolerance: {diffs[:3]}")
                else:
                    details.append(f"{name}: bytes differ but numerically equal within tolerance")
            else:
                ok = False
                details.append(f"{name}: content hash mismatch")
        new_m = ReproducibilityManifest.model_validate_json((new_dir / "manifest.json").read_text(encoding="utf-8"))
        for fld in ("evidence_resolution_sha256", "evidence_registry_sha256"):
            if getattr(manifest, fld) != getattr(new_m, fld):
                ok = False
                details.append(f"{fld}: recorded {getattr(manifest, fld)}, now {getattr(new_m, fld)}")
        for dep, ver in manifest.dependencies.items():
            if new_m.dependencies.get(dep) != ver:
                details.append(
                    f"dependency {dep}: recorded {ver}, now {new_m.dependencies.get(dep)} (platform/library drift)"
                )
    return ReproduceOutcome(ok, exact and ok, details)


@dataclass(frozen=True)
class EvidenceSensitiveRun:
    outcome: EvidenceDiscoveryOutcome
    manifest: EvidenceSensitivityManifest | None
    report_markdown: str | None


def evaluate_discovery_evidence(
    spec: DiscoverySpec,
    registry: DatasetRegistry,
    out_dir: str | Path | None = None,
    **kwargs: Any,
) -> EvidenceSensitiveRun:
    """Discovery -> evidence-sensitive evaluation -> manifest + report; optionally written as canonical artifacts.

    ``kwargs`` are those of ``discovery.evidence_discovery.evaluate_discovery_with_evidence`` (settings, perturbation
    ranges, falsification config). Artifacts: ``evidence_sensitivity.json`` (the manifest, key-sorted JSON) and
    ``evidence_report.md``, both pure functions of the evaluations (no clock, host or path).
    """
    outcome = evaluate_discovery_with_evidence(spec, registry=registry, **kwargs)
    manifest = (
        build_evidence_manifest(spec.discovery_id, outcome.evaluations, kwargs.get("config"))
        if outcome.evaluations
        else None
    )
    report = (
        render_evidence_sensitivity_report(
            f"Evidence-sensitive evaluation: {spec.discovery_id}", outcome.evaluations, manifest
        )
        if manifest is not None
        else None
    )
    if out_dir is not None and manifest is not None and report is not None:
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        _write(d / "evidence_sensitivity.json", _dump(manifest))
        _write(d / "evidence_report.md", report)
    return EvidenceSensitiveRun(outcome, manifest, report)
