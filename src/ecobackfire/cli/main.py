"""`ecobackfire` command-line interface (argparse; no extra dependency)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ecobackfire import __version__, api
from ecobackfire.contracts.verification import VerificationReport
from ecobackfire.errors import DatasetValidationError, EcoBackfireError
from ecobackfire.models.registry import get_model, list_models


def _cmd_run(a: argparse.Namespace) -> int:
    config = api.load_config(a.config)
    run_dir = api.execute(
        config, a.out, verify=not a.no_verify, source_config_path=str(a.config), registry=a.registry
    )
    print(f"run directory: {run_dir}")
    vpath = run_dir / "verification.json"
    if vpath.exists():
        v = VerificationReport.model_validate_json(vpath.read_text(encoding="utf-8"))
        print(f"label: {v.recommended_label}")
        if v.claim:
            print(f"claim: {v.claim}")
        for c in v.checks:
            print(f"  [{c.status:>13}] {c.check_id}")
    return 0


def _cmd_verify(a: argparse.Namespace) -> int:
    rd = api.reverify(a.run_dir, registry=a.registry)
    print(f"re-verified: {rd}")
    return 0


def _cmd_reproduce(a: argparse.Namespace) -> int:
    out = api.reproduce(a.manifest, registry=a.registry)
    for d in out.details:
        print(d)
    if out.ok:
        print("REPRODUCED " + ("(byte-identical artifacts)" if out.exact else "(within documented tolerance)"))
        return 0
    print("NOT REPRODUCED")
    return 1


def _cmd_resolve(a: argparse.Namespace) -> int:
    """Show how each evidence request of a configuration resolves, without running the model."""
    from ecobackfire.datasets.registry import DatasetRegistry
    from ecobackfire.datasets.resolution import describe_unresolved, resolve_requests

    config = api.load_config(a.config)
    if not config.evidence_requests:
        print("this configuration has no evidence_requests; every input is a binding (assumed or illustrative)")
        return 0
    reg = DatasetRegistry.from_directory(a.registry, strict=True)
    res = resolve_requests(reg, get_model(config.model_ref).spec, config.bindings, config.evidence_requests)
    for vid, r in sorted(res.resolutions.items()):
        if r.state == "resolved" and r.selected is not None:
            s = r.selected
            conv = s.conversion
            unit_txt = (
                f"{conv.original_value:g} {conv.original_unit} -> {conv.canonical_value:g} {conv.canonical_unit}"
                if conv.performed
                else f"{conv.canonical_value:g} {conv.canonical_unit}"
            )
            print(f"{vid}: resolved by {s.evidence_id} [{s.status}] {unit_txt} (rule: {r.selection_rule})")
            for w in r.warnings:
                print(f"    warning: {w}")
        else:
            print(describe_unresolved(r))
    return 0 if not res.unresolved() else 1


def _cmd_mission(a: argparse.Namespace) -> int:
    """Plan and execute a research mission with the existing deterministic machinery."""
    from ecobackfire.mission.loader import load_mission
    from ecobackfire.mission.planner import plan_mission
    from ecobackfire.mission.runner import _load_registry, run_mission

    mission = load_mission(a.mission)
    if a.plan_only:
        reg, err = _load_registry(a.registry)
        if err:
            print(f"error: {err}", file=sys.stderr)
            return 1
        plan = plan_mission(mission, reg).plan
        print(plan.model_dump_json(indent=2))
        return 0 if plan.status == "ready" else 1
    run = run_mission(mission, a.out, a.registry, source_path=str(a.mission))
    r = run.result
    print(f"mission: {mission.mission_id}")
    print(f"status: {r.status}" + (f" (reason: {r.reason})" if r.reason else ""))
    if r.failure:
        print(f"failure at stage '{r.failure.stage}': {r.failure.message}")
        for d in r.failure.details:
            print(f"  - {d}")
    for st in r.stages:
        print(f"  [{st.status:>11}] {st.stage}" + (f": {st.detail}" if st.detail else ""))
    for c in r.candidates:
        cls = c.robustness_classification or "not classified"
        print(f"  #{c.rank} {c.candidate.candidate_id} = {c.candidate.value:.6g} {c.candidate.unit}  [{cls}]  "
              f"(attention score {c.ranking_score:.3f}; not a probability)")
    if r.evidence_summary is not None:
        print(f"evidence summary: {r.evidence_summary.statement}")
    if r.backfire is not None:
        b = r.backfire
        print(
            f"backfire hunt: {b.status}; baseline {b.baseline.regime}; {b.evaluations_performed}/{b.max_evaluations} "
            f"evaluations; searched {', '.join(b.parameters_searched) or 'nothing'}; "
            f"{b.candidates_found} crossing(s) found, "
            f"{len(b.candidates_falsified)} falsified, {len(b.survived_robustness)} survived robustness, "
            f"{len(b.evidence_sensitive)} evidence-sensitive"
        )
        print(f"  {b.strongest_finding_statement}")
    if r.analysis_settings is not None:
        print(f"challenge scope: {r.analysis_settings.scope_statement}")
        print(f"settings audit: {r.analysis_settings.audit.status} ({r.analysis_settings.audit.n_warnings} warning(s))")
    if r.verification_disclosure is not None:
        print(f"verification: {r.verification_disclosure.statement}")
    if r.budget_usage is not None:
        print(f"computation: {r.budget_usage.statement}")
    print(f"reproducibility check: {r.reproducibility.check_outcome}")
    if run.run_dir is not None:
        print(f"run directory: {run.run_dir}")
    return 0 if r.status in ("completed", "completed_no_candidates") else 1


def _cmd_mission_reproduce(a: argparse.Namespace) -> int:
    from ecobackfire.mission.runner import reproduce_mission

    out = reproduce_mission(a.manifest, registry=a.registry)
    for d in out.details:
        print(d)
    if out.ok:
        print("REPRODUCED " + ("(byte-identical artifacts)" if out.exact else "(within documented tolerance)"))
        return 0
    print("NOT REPRODUCED")
    return 1


def _cmd_models(a: argparse.Namespace) -> int:
    if a.action == "list":
        for ref in list_models():
            print(ref)
        return 0
    spec = get_model(a.ref).spec
    print(spec.model_dump_json(indent=2))
    return 0


def _cmd_datasets(a: argparse.Namespace) -> int:
    # Imported lazily: dataset commands need pydantic/pint like everything else, but keep CLI start-up light.
    from ecobackfire.datasets.ingest import build_release, load_import_spec, verify_raw_files, write_release
    from ecobackfire.datasets.registry import DatasetRegistry

    if a.action == "import":
        spec = load_import_spec(a.spec)
        model = get_model(spec.target_model_ref).spec if spec.target_model_ref else None
        result = build_release(spec, a.raw_root, model_spec=model)
        rel = result.release
        print(
            f"validated {rel.dataset_id}@{rel.release_version}: {len(rel.records)} record(s), 0 value transformations"
        )
        print(f"release content sha256: {result.report['release_content_sha256']}")
        if a.dry_run:
            print("dry run: nothing written")
            return 0
        path, new = write_release(result, a.root)
        print(("written: " if new else "unchanged (identical release already present): ") + str(path))
        return 0

    target = getattr(a, "target", None)
    if target and "@" in target and not Path(target).exists():
        reg = DatasetRegistry.from_directory(a.root, strict=True)
        ds, ver = target.split("@", 1)
        releases = [reg.get(ds, ver)]
    else:
        reg = DatasetRegistry.from_directory(target or a.root, strict=True)
        releases = reg.releases()
    if a.action == "list":
        if not releases:
            print("no dataset releases found")
        for lr in releases:
            r = lr.release
            variables = sorted({rec.variable_id for rec in r.records})
            statuses = sorted({rec.status().value for rec in r.records})
            print(
                f"{r.dataset_id}@{r.release_version}  quality={r.quality.value}  records={len(r.records)}  "
                f"variables={','.join(variables) or '-'}  status={','.join(statuses) or '-'}"
            )
        return 0
    problems: list[str] = []
    for lr in releases:
        problems += verify_raw_files(lr.release, a.raw_root)
    if problems:
        raise DatasetValidationError("raw file verification failed:\n- " + "\n- ".join(problems))
    for lr in releases:
        r = lr.release
        print(
            f"OK {r.dataset_id}@{r.release_version}: schema valid, {len(r.records)} record(s), "
            f"{len(r.raw_files)} raw file hash(es) verified, file sha256 {lr.file_sha256[:16]}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ecobackfire", description="EcoBackfire environmental stress-testing engine")
    p.add_argument("--version", action="version", version=f"ecobackfire {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run (and verify) an experiment from a TOML config")
    r.add_argument("config", type=Path)
    r.add_argument("--out", type=Path, default=Path("runs"))
    r.add_argument("--no-verify", action="store_true")
    r.add_argument("--registry", type=Path, default=Path("data/releases"), help="dataset registry (evidence runs only)")
    r.set_defaults(fn=_cmd_run)
    v = sub.add_parser("verify", help="re-run verification on an existing run directory")
    v.add_argument("run_dir", type=Path)
    v.add_argument("--registry", type=Path, default=Path("data/releases"), help="dataset registry (evidence runs only)")
    v.set_defaults(fn=_cmd_verify)
    rp = sub.add_parser("reproduce", help="re-run from a manifest and compare artifact hashes")
    rp.add_argument("manifest", type=Path)
    rp.add_argument("--registry", type=Path, default=Path("data/releases"), help="dataset registry (evidence only)")
    rp.set_defaults(fn=_cmd_reproduce)
    rs = sub.add_parser("resolve", help="show how a config's evidence requests resolve (no model run)")
    rs.add_argument("config", type=Path)
    rs.add_argument("--registry", type=Path, default=Path("data/releases"))
    rs.set_defaults(fn=_cmd_resolve)
    ms = sub.add_parser("mission", help="plan and run a research mission (evidence to classification)")
    ms.add_argument("mission", type=Path)
    ms.add_argument("--out", type=Path, default=Path("runs"))
    ms.add_argument("--registry", type=Path, default=Path("data/releases"))
    ms.add_argument("--plan-only", action="store_true", help="print the investigation plan; run nothing")
    ms.set_defaults(fn=_cmd_mission)
    mr = sub.add_parser("mission-reproduce", help="re-run a mission from its mission_manifest.json and compare hashes")
    mr.add_argument("manifest", type=Path)
    mr.add_argument("--registry", type=Path, default=Path("data/releases"))
    mr.set_defaults(fn=_cmd_mission_reproduce)
    m = sub.add_parser("models", help="list or describe registered models")
    msub = m.add_subparsers(dest="action", required=True)
    msub.add_parser("list").set_defaults(fn=_cmd_models)
    d = msub.add_parser("describe")
    d.add_argument("ref")
    d.set_defaults(fn=_cmd_models)
    ds = sub.add_parser("datasets", help="list, validate or import local dataset releases")
    ds.add_argument("--root", type=Path, default=Path("data/releases"), help="dataset registry root")
    ds.add_argument("--raw-root", type=Path, default=Path("data/raw"), help="directory holding pinned raw files")
    dsub = ds.add_subparsers(dest="action", required=True)
    dl = dsub.add_parser("list")
    dl.add_argument("target", nargs="?")
    dl.set_defaults(fn=_cmd_datasets)
    dv = dsub.add_parser("validate", help="validate schema, provenance, units and raw-file hashes")
    dv.add_argument("target", nargs="?", help="<dataset_id>@<version> or a registry root directory (default: all)")
    dv.set_defaults(fn=_cmd_datasets)
    di = dsub.add_parser("import", help="import a real source file into the registry from an import spec")
    di.add_argument("spec", type=Path)
    di.add_argument("--dry-run", action="store_true")
    di.set_defaults(fn=_cmd_datasets)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args))
    except EcoBackfireError as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
