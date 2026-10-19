"""Evidence resolution wired into run, verify, reproduce and discovery (SYNTHETIC registries)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _resolution_fixtures import DS, VER, build_registry, f_record, request
from conftest import EXAMPLES, make_config

from ecobackfire import api
from ecobackfire.contracts.discovery import DiscoverySpec
from ecobackfire.contracts.verification import ReproducibilityManifest
from ecobackfire.core.engine import prepare_inputs
from ecobackfire.discovery import robustness as rb
from ecobackfire.discovery.engine import run_discovery
from ecobackfire.discovery.provenance import evidence_ids_for, provenance_digest
from ecobackfire.errors import ConfigError, EvidenceConflictError, EvidenceNotAvailableError
from ecobackfire.experiments.evidence import alternative_configs, materialize_config
from ecobackfire.experiments.runner import config_sha256, get_metric, make_g, run_experiment
from ecobackfire.models.registry import get_model

pytestmark = pytest.mark.software
EID = f"{DS}@{VER}:f.in.2024"


def evidence_config(e2_config, **req):  # type: ignore[no-untyped-def]
    bindings = {k: v.model_dump(mode="json") for k, v in e2_config.bindings.items() if k != "f"}
    return make_config(e2_config, bindings=bindings, evidence_requests={"f": request(**req)})


@pytest.fixture
def reg(tmp_path: Path):  # type: ignore[no-untyped-def]
    return build_registry(tmp_path / "reg", [f_record()])


def test_evidence_backed_run_uses_the_resolved_value_through_the_normal_engine(e2_config, reg) -> None:  # type: ignore[no-untyped-def]
    cfg = evidence_config(e2_config)
    res = run_experiment(cfg, registry=reg)
    assert res.base_inputs["f"].value == pytest.approx(0.7054, rel=1e-12)
    # no parallel path: the same numbers via a hand-written (illustrative) binding give the same root
    manual = make_config(
        e2_config,
        bindings={
            **{k: v.model_dump(mode="json") for k, v in e2_config.bindings.items() if k != "f"},
            "f": {
                **e2_config.bindings["f"].model_dump(mode="json"),
                "value": 0.7054,
                "uncertainty": {"kind": "unknown"},
            },
        },
    )
    ref = run_experiment(manual)
    assert res.breakeven.roots[0].value == pytest.approx(ref.breakeven.roots[0].value, rel=1e-12)  # type: ignore[union-attr]


def test_resolution_table_lists_every_input_and_its_basis(e2_config, reg) -> None:  # type: ignore[no-untyped-def]
    res = run_experiment(evidence_config(e2_config), registry=reg)
    ev = res.evidence_resolution
    assert ev is not None
    rows = ev.by_variable()
    assert rows["f"].basis == "evidence" and rows["f"].selected.evidence_id == EID  # type: ignore[union-attr]
    assert {v for v, r in rows.items() if r.basis == "illustrative"} == set(e2_config.bindings) - {"f"}
    assert ev.n_evidence == 1 and ev.n_illustrative == 8 and ev.n_assumed == 0
    assert ev.evidence_backed_inputs == []  # unreviewed, no uncertainty: never called evidence-backed
    assert res.weakest_provenance.value == "illustrative"
    assert any("inputs without evidence" in w for w in res.warnings)


def test_sourced_binding_without_resolution_is_still_refused(e2_config) -> None:  # type: ignore[no-untyped-def]
    spec = get_model("efficiency_rebound@1.0.0").spec
    from conftest import with_binding

    cfg = with_binding(e2_config, "f", provenance_class="sourced", evidence_record_id="made-up:1", note=None)
    with pytest.raises(EvidenceNotAvailableError):
        run_experiment(cfg)
    with pytest.raises(EvidenceNotAvailableError):  # a forged id that the caller did not verify
        prepare_inputs(spec, cfg.bindings, ["f"], verified_evidence={"f": "other:1"})


def test_conflict_aborts_a_run_and_alternatives_can_each_be_run(e2_config, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    reg = build_registry(tmp_path, [f_record("a", value=300.0), f_record("b", value=900.0)])
    cfg = evidence_config(e2_config)
    with pytest.raises(EvidenceConflictError):
        run_experiment(cfg, registry=reg)
    alts = alternative_configs(cfg, reg, "f")
    assert sorted(alts) == [f"{DS}@{VER}:a", f"{DS}@{VER}:b"]
    roots = {k: run_experiment(c, registry=reg).breakeven.roots[0].value for k, c in alts.items()}  # type: ignore[union-attr]
    assert roots[f"{DS}@{VER}:a"] != roots[f"{DS}@{VER}:b"]  # a different source gives a different answer, visibly
    res = run_experiment(alts[f"{DS}@{VER}:b"], registry=reg)
    assert res.evidence_resolution.by_variable()["f"].conflict.overridden_by_caller  # type: ignore[union-attr]


def test_effective_config_hash_pins_the_resolved_value(e2_config, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    cfg = evidence_config(e2_config)
    r1 = build_registry(tmp_path / "a", [f_record(value=705.4)])
    r2 = build_registry(tmp_path / "b", [f_record(value=500.0)])
    h1 = config_sha256(materialize_config(cfg, r1)[0])
    h2 = config_sha256(materialize_config(cfg, r2)[0])
    assert h1 != h2 and h1 == config_sha256(materialize_config(cfg, r1)[0])  # deterministic


def test_provenance_reaches_artifacts_manifest_and_report(e2_config, reg, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    rd = api.execute(evidence_config(e2_config), tmp_path / "runs", registry=reg)
    stored = json.loads((rd / "config.json").read_text())
    assert stored["bindings"]["f"]["evidence_record_id"] == EID and "f" in stored["evidence_requests"]
    result = json.loads((rd / "result.json").read_text())
    sel = result["evidence_resolution"]["inputs"][
        [i["variable_id"] for i in result["evidence_resolution"]["inputs"]].index("f")
    ]["selected"]
    assert sel["source"]["source_id"] == "fixture:not-a-real-source" and sel["conversion"]["performed"] is True
    m = ReproducibilityManifest.model_validate_json((rd / "manifest.json").read_text())
    assert m.dataset_release_ref and DS in m.dataset_release_ref
    assert m.evidence_resolution_sha256 and m.evidence_registry_sha256 == reg.sha256()
    report = (rd / "report.md").read_text()
    assert "## Evidence resolution" in report and EID in report and "705.4 gCO2e/kWh -> 0.7054 kgCO2e/kWh" in report
    ver = json.loads((rd / "verification.json").read_text())
    f7 = next(c for c in ver["checks"] if c["check_id"].startswith("F7"))
    assert f7["status"] == "limits_claim" and f7["details"]["evidence_resolved_inputs"] == ["f"]
    assert ver["recommended_label"] == "conditional"
    assert next(c for c in ver["checks"] if c["check_id"].startswith("V3"))["status"] == "passed"


def test_verify_and_reproduce_with_and_without_the_registry(e2_config, reg, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    rd = api.execute(evidence_config(e2_config), tmp_path / "runs", registry=reg)
    out = api.reproduce(rd / "manifest.json", registry=reg)
    assert out.ok and out.exact
    api.reverify(rd, registry=reg)
    with pytest.raises(ConfigError):  # an evidence-backed run cannot be re-verified without its registry
        api.reverify(rd)
    assert not api.reproduce(rd / "manifest.json", registry=None).ok


def test_reproduce_reports_a_changed_registry_instead_of_passing(e2_config, reg, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    rd = api.execute(evidence_config(e2_config), tmp_path / "runs", registry=reg)
    changed = build_registry(tmp_path / "other", [f_record(value=800.0)])
    out = api.reproduce(rd / "manifest.json", registry=changed)
    assert not out.ok and any("re-resolved" in d or "changed" in d for d in out.details)


# ---------------------------------------------------------------- E2 regression: no evidence, nothing changes
def test_experiments_without_evidence_are_unchanged(e2_config, reg, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    assert config_sha256(e2_config).startswith("ec25be88")
    assert "evidence_requests" not in e2_config.model_dump(mode="json")
    plain = run_experiment(e2_config)
    with_reg = run_experiment(e2_config, registry=reg)  # a registry that is not needed changes nothing
    assert plain.model_dump(mode="json") == with_reg.model_dump(mode="json")
    assert "evidence_resolution" not in plain.model_dump(mode="json")
    assert plain.weakest_provenance.value == "illustrative" and plain.breakeven.roots[0].value == pytest.approx(  # type: ignore[union-attr]
        1.11275746800796, rel=1e-12
    )
    rd = api.execute(e2_config, tmp_path / "r", registry=reg)
    m = json.loads((rd / "manifest.json").read_text())
    assert "evidence_resolution_sha256" not in m and m["dataset_release_ref"] is None


# ---------------------------------------------------------------- discovery -> candidate -> robustness join
def _spec(e2_config, reg_requests=True):  # type: ignore[no-untyped-def]
    d = {
        "discovery_id": "t_evidence",
        "title": "t",
        "research_question": "t",
        "model_ref": e2_config.model_ref,
        "options": dict(e2_config.options),
        "decision_metric_ref": e2_config.decision_metric_ref,
        "bindings": {k: v.model_dump(mode="json") for k, v in e2_config.bindings.items() if k != "f"},
        "evidence_requests": {"f": request()},
        "parameters": [
            {
                "variable_id": "epsilon",
                "low": 0.0,
                "high": 3.0,
                "basis": "ILLUSTRATIVE — not evidence: demonstration range",
                "provenance_class": "illustrative",
            }
        ],
    }
    return DiscoverySpec.model_validate(d)


def test_provenance_survives_resolution_to_candidate_to_robustness(e2_config, reg) -> None:  # type: ignore[no-untyped-def]
    res = run_discovery(_spec(e2_config), registry=reg)
    assert res.evidence_resolution is not None and res.evidence_resolution.n_evidence == 1
    assert len(res.candidates) == 1
    cand = res.candidates[0]
    refs = {r.variable_id: r for r in cand.input_evidence}
    assert set(refs) == {"E0", "EM0", "EM1", "L", "epsilon", "eta", "f", "phi"}
    assert refs["f"].evidence_id == EID and refs["f"].role == "held_at_base" and refs["f"].basis == "evidence"
    assert refs["f"].conversion_performed is True and refs["f"].canonical_value == pytest.approx(0.7054)
    assert refs["epsilon"].role == "searched" and refs["epsilon"].evidence_id is None
    assert refs["eta"].basis == "illustrative"
    assert cand.weakest_input_provenance.value == "illustrative" and cand.label == "candidate"
    assert any("without evidence" in w for w in res.warnings)
    # join keys for falsification / robustness
    ids = evidence_ids_for(cand.input_evidence, ["f", "eta"])
    assert ids == {"f": (EID,), "eta": ()}
    assert provenance_digest(cand.input_evidence) == provenance_digest(list(reversed(cand.input_evidence)))
    model = get_model(res.model_ref)
    opts = model.resolve_options(res.options)
    metric = get_metric(model, res.decision_metric_ref)
    base = {k: v.value for k, v in res.base_inputs.items()}
    spec = rb.relative_spec("f", "kgCO2e/kWh", base["f"], 0.05, evidence_ids=ids["f"])
    root = rb.find_breakevens(make_g(model, opts, metric, base, "epsilon"), 0.0, 3.0, 99, 1e-12).roots[0]
    rep = rb.analyze_robustness(
        lambda over: make_g(model, opts, metric, {**base, **over}, "epsilon"),
        base,
        "epsilon",
        root,
        0.0,
        3.0,
        grid_points=99,
        xtol=1e-12,
        perturbations=[spec],
    )
    assert rep.parameters[0].evidence_ids == (EID,)
    assert (
        rep.analysis_kind == "configured_sensitivity_experiment"
    )  # carrying an id does not upgrade a configured range


def test_discovery_without_evidence_has_no_evidence_fields(e2_config) -> None:  # type: ignore[no-untyped-def]
    d = _spec(e2_config).model_dump(mode="json")
    d.pop("evidence_requests")
    d["bindings"]["f"] = e2_config.bindings["f"].model_dump(mode="json")
    res = run_discovery(DiscoverySpec.model_validate(d))
    dumped = res.model_dump(mode="json")
    assert "evidence_resolution" not in dumped and all("input_evidence" not in c for c in dumped["candidates"])


def test_discovery_conflict_and_missing_raise_instead_of_guessing(e2_config, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    reg = build_registry(tmp_path, [f_record("a", value=300.0), f_record("b", value=900.0)])
    with pytest.raises(EvidenceConflictError):
        run_discovery(_spec(e2_config), registry=reg)
    with pytest.raises(ConfigError, match="registry"):
        run_discovery(_spec(e2_config))


# ---------------------------------------------------------------- CLI
def test_cli_resolve_run_verify_reproduce(e2_config, reg, tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    from ecobackfire.cli.main import main

    # the CLI loads TOML; write one by reusing the shipped example with the synthetic dataset id
    toml = (EXAMPLES.parent / "evidence" / "e2_eps_f_from_evidence.toml").read_text()
    toml = toml.replace('"owid-electricity-carbon-intensity"', f'"{DS}"')
    p = tmp_path / "cfg.toml"
    p.write_text(toml)
    root = tmp_path / "reg"
    assert main(["resolve", str(p), "--registry", str(root)]) == 0
    assert "resolved by" in capsys.readouterr().out
    assert main(["run", str(p), "--registry", str(root), "--out", str(tmp_path / "runs")]) == 0
    rd = next((tmp_path / "runs").iterdir())
    assert main(["verify", str(rd), "--registry", str(root)]) == 0
    assert main(["run", str(p), "--registry", str(tmp_path / "empty")]) == 2  # unreadable registry is an error


def test_cli_resolve_exit_code_for_unresolved(e2_config, tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    from ecobackfire.cli.main import main

    build_registry(tmp_path / "reg", [f_record(geo="FR")])
    toml = (EXAMPLES.parent / "evidence" / "e2_eps_f_from_evidence.toml").read_text()
    p = tmp_path / "cfg.toml"
    p.write_text(toml.replace('"owid-electricity-carbon-intensity"', f'"{DS}"'))
    assert main(["resolve", str(p), "--registry", str(tmp_path / "reg")]) == 1
    assert "geography_mismatch" in capsys.readouterr().out
