"""Acceptance tests mapped to docs/implementation-roadmap.md Phase 1 criteria and the brief section 9."""

import json
import re
import socket
from pathlib import Path

import pytest
from conftest import EXAMPLES, make_config, with_binding

from ecobackfire import api
from ecobackfire.cli.main import main
from ecobackfire.contracts.verification import ReproducibilityManifest, VerificationReport
from ecobackfire.experiments.runner import run_experiment

FORBIDDEN = re.compile(r"\b(proves?|proven|discovered|novel|validated|established fact)\b", re.I)
ARTIFACTS = ["config.json", "result.json", "verification.json", "report.md"]


def run(cfg_path: Path, out: Path) -> Path:
    cfg = api.load_config(cfg_path)
    return api.execute(cfg, out, source_config_path=str(cfg_path))


@pytest.fixture()
def offline(monkeypatch):
    """Any attempt to open a network socket, or to read provider credentials, fails the test."""

    def boom(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_core_runs_offline_without_api_keys(offline, tmp_path):
    rd = run(EXAMPLES / "e2_breakeven_eps.toml", tmp_path)
    assert (rd / "result.json").exists() and (rd / "manifest.json").exists()


def test_identical_inputs_give_byte_identical_artifacts(tmp_path):
    a = run(EXAMPLES / "e2_breakeven_eps.toml", tmp_path / "a")
    b = run(EXAMPLES / "e2_breakeven_eps.toml", tmp_path / "b")
    for name in ARTIFACTS:
        assert (a / name).read_bytes() == (b / name).read_bytes(), name


@pytest.mark.parametrize("cfg", ["e1_compare", "e2_breakeven_eps", "e3_breakeven_f", "e7_oat_breakeven_eps"])
def test_every_example_runs_and_is_reproducible_from_its_manifest(cfg, tmp_path):
    rd = run(EXAMPLES / f"{cfg}.toml", tmp_path)
    out = api.reproduce(rd / "manifest.json")
    assert out.ok and out.exact, out.details
    m = ReproducibilityManifest.model_validate_json((rd / "manifest.json").read_text())
    assert m.config_sha256 and set(m.artifact_hashes) == set(ARTIFACTS)
    assert m.dependencies["scipy"] != "not installed" and m.model_ref == "efficiency_rebound@1.0.0"


def test_tampered_config_is_detected_by_reproduce(tmp_path):
    rd = run(EXAMPLES / "e2_breakeven_eps.toml", tmp_path)
    p = rd / "config.json"
    data = json.loads(p.read_text())
    data["bindings"]["eta"]["value"] = 0.31
    p.write_text(json.dumps(data))
    assert not api.reproduce(rd / "manifest.json").ok


def test_tampered_result_is_detected_by_reproduce(tmp_path):
    rd = run(EXAMPLES / "e2_breakeven_eps.toml", tmp_path)
    p = rd / "result.json"
    data = json.loads(p.read_text())
    data["breakeven"]["roots"][0]["value"] += 0.01
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    out = api.reproduce(rd / "manifest.json")
    assert not out.ok
    assert any("result.json" in d for d in out.details)


def test_report_content_and_language(tmp_path):
    rd = run(EXAMPLES / "e2_breakeven_eps.toml", tmp_path)
    text = (rd / "report.md").read_text(encoding="utf-8")
    assert "ILLUSTRATIVE — not evidence" in text  # banner
    assert "Functional unit" in text and "System boundary" in text and "Limitations" in text
    assert "ecobackfire run" in text and "sha256" in text
    assert "`conditional`" in text and "Prior-art" in text
    assert "illustrative" in text
    assert not FORBIDDEN.search(text), FORBIDDEN.search(text).group(0)
    assert "Recommended label: `conditional`" in text
    assert "Recommended label: `verified_internal`" not in text
    rep = VerificationReport.model_validate_json((rd / "verification.json").read_text())
    assert rep.recommended_label == "conditional"


def test_uncertainty_is_shown_not_hidden(tmp_path, e2_config):
    cfg = with_binding(e2_config, "L", uncertainty={"kind": "unknown"})
    rd = api.execute(cfg, tmp_path)
    text = (rd / "report.md").read_text(encoding="utf-8")
    assert re.search(r"\| `L` \| 12 \| yr \| illustrative \| unknown \|", text)
    assert re.search(r"\| `E0` \| 3000 \| kWh/yr \| illustrative \| interval \[2000, 4500\] \|", text)
    f7 = next(c for c in json.loads((rd / "verification.json").read_text())["checks"] if c["check_id"].startswith("F7"))
    assert "L" in f7["details"]["inputs_with_unknown_uncertainty"]


def test_system_boundary_changes_are_visible_in_reports(tmp_path, e2_config):
    texts, roots = {}, {}
    for b in ("B1", "B2", "B3"):
        cfg = make_config(
            e2_config, experiment_id=f"e2_{b}", options={"boundary_variant": b, "demand_form": "constant_elasticity"}
        )
        rd = api.execute(cfg, tmp_path)
        texts[b] = (rd / "report.md").read_text(encoding="utf-8")
        roots[b] = json.loads((rd / "result.json").read_text())["breakeven"]["roots"][0]["value"]
        assert f"System boundary `{b}`" in texts[b]
    assert roots["B2"] < roots["B1"] < roots["B3"]
    assert "old device's embodied emissions are sunk" in texts["B2"]
    assert "operational emissions only" in texts["B3"]


def test_no_crossing_case_reports_already_non_beneficial_not_a_negative_root(e2_config):
    cfg = with_binding(e2_config, "EM1", value=9000.0)  # r = (9000-1000)/(12*0.4*3000) = 0.556 > eta = 0.3
    res = run_experiment(cfg)
    assert res.breakeven.roots == []
    assert res.breakeven.classification == "no_crossing_never_beneficial"
    assert "NOT beneficial" in res.breakeven.note


def test_no_claim_means_not_applicable_label(tmp_path, e2_config):
    cfg = with_binding(e2_config, "EM1", value=9000.0)
    rd = api.execute(cfg, tmp_path)
    rep = VerificationReport.model_validate_json((rd / "verification.json").read_text())
    assert rep.claim is None and rep.recommended_label == "not_applicable"


def test_cli_end_to_end_and_error_codes(tmp_path, capsys):
    assert main(["run", str(EXAMPLES / "e2_breakeven_eps.toml"), "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "label: conditional" in out and "epsilon = 1.1127574" in out
    manifest = next(tmp_path.glob("*/manifest.json"))
    assert main(["reproduce", str(manifest)]) == 0
    assert main(["run", str(tmp_path / "nope.toml")]) == 2  # user error -> exit code 2, no traceback
    bad = tmp_path / "bad.toml"
    bad.write_text("not = [valid")
    assert main(["run", str(bad)]) == 2
    assert main(["models", "list"]) == 0
    assert main(["models", "describe", "efficiency_rebound@1.0.0"]) == 0


def test_invalid_unit_in_toml_fails_cleanly(tmp_path, capsys):
    text = (EXAMPLES / "e2_breakeven_eps.toml").read_text().replace('unit = "kWh/yr"', 'unit = "kg"', 1)
    p = tmp_path / "bad_units.toml"
    p.write_text(text, encoding="utf-8")
    assert main(["run", str(p), "--out", str(tmp_path / "o")]) == 2
    assert "UnitError" in capsys.readouterr().err
