"""Manifest reproduction, report content, the report-hash regression, scoring axes, and the REAL f record.
Pure: no pint, no pytest import. Synthetic fixtures except where a test says REAL."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _evidence_sensitivity_fixtures import UNIT, record, resolution
from test_evidence_sensitivity import S, make, run

from ecobackfire.core.hashing import canonical_json, canonicalize, pretty_json
from ecobackfire.discovery import scoring
from ecobackfire.discovery.evidence_scenarios import build_scenario_set
from ecobackfire.reporting.evidence_manifest import build_evidence_manifest, reproduce_evidence_evaluation
from ecobackfire.reporting.evidence_sensitivity_report import render_evidence_sensitivity_report
from ecobackfire.reporting.formatting import format_kv

REPO = Path(__file__).resolve().parents[2]


def _evals():
    a, b = record("a", 1.0, low=0.9, high=2.5, source="A"), record("b", 1.2, source="B")
    return [run([S(a, b, select=a.evidence_id)], perturb_vars=["q"])]


def test_manifest_is_deterministic_and_has_no_clock_or_path():
    m1, m2 = build_evidence_manifest("t", _evals()), build_evidence_manifest("t", _evals())
    assert m1.model_dump_json() == m2.model_dump_json()
    text = m1.model_dump_json()
    assert "/home/" not in text and "T00:" not in text
    assert m1.datasets[0].dataset_id == "synthetic-fixture" and m1.datasets[0].version == "0.0.1"
    assert m1.source_keys == ["A", "B"] and "C7" in m1.classification_rules


def test_manifest_records_settings_ranges_selections_and_results():
    ev = build_evidence_manifest("t", _evals()).evaluations[0]
    assert ev.scenario_sets[0].ranges and ev.scenario_sets[0].baseline_evidence_id
    assert ev.scenario_sets[0].resolution_decision == "caller_selected"
    assert ev.falsification_settings and "perturbations" in ev.robustness_settings
    assert ev.outcomes and ev.decision.rule_id


def test_reproduce_from_serialized_manifest():
    from ecobackfire.contracts.evidence_sensitivity import EvidenceSensitivityManifest
    m = build_evidence_manifest("t", _evals())
    loaded = EvidenceSensitivityManifest.model_validate_json(m.model_dump_json())  # JSON round trip
    out = reproduce_evidence_evaluation(loaded, _evals)
    assert out.ok and out.exact, out.details


def test_reproduce_detects_changed_inputs():
    m = build_evidence_manifest("t", _evals())
    a = record("a", 1.0, low=0.9, high=2.4, source="A")  # a different documented range
    out = reproduce_evidence_evaluation(
        m, lambda: [run([S(a, record("b", 1.2, source="B"), select=a.evidence_id)], perturb_vars=["q"])]
    )
    assert not out.ok and "inputs_sha256" in out.details[0]


def test_reproduce_detects_changed_results():
    m = build_evidence_manifest("t", _evals())
    forged = m.model_copy(update={"results_sha256": "0" * 64})
    out = reproduce_evidence_evaluation(forged, _evals)
    assert out.ok and not out.exact  # numbers agree; the recorded hash was wrong: reported, not hidden
    assert out.details


def test_report_has_all_sections_and_the_two_statements():
    ev = _evals()
    md = render_evidence_sensitivity_report("T", ev, build_evidence_manifest("t", ev))
    for h in ("### Evidence basis", "### Evidence scenarios", "### Sensitivity", "### Falsification",
              "### Robustness", "### Classification", "### Limitations"):
        assert h in md, h
    assert "The model produces this result under these documented inputs." in md
    assert "does NOT establish that the real world behaves this way" in md
    assert "synthetic-fixture@0.0.1" in md and "evidence_range_sensitivity" in md and "not confidence" in md
    assert md == render_evidence_sensitivity_report("T", ev, build_evidence_manifest("t", ev))


def test_report_hash_regression_key_order_after_json_round_trip():
    """verify -> reproduce reported a report.md hash mismatch: trace outputs rendered in insertion order in memory but
    in sorted order after the key-sorted JSON round trip. Rendering must depend on keys/values only."""
    step_out = {"r": 0.05, "dC_normalized": -4.16e-17}  # EQ6's real insertion order
    reloaded = json.loads(pretty_json(step_out))
    assert list(step_out) != list(reloaded)  # the round trip really does reorder
    fmt = lambda v: f"{float(v):.10g}"  # noqa: E731
    assert format_kv(step_out, fmt) == format_kv(reloaded, fmt) == "dC_normalized=-4.16e-17, r=0.05"
    overrides = {"eps": 1.2, "L": 12.0}
    assert format_kv(overrides, str) == format_kv(json.loads(pretty_json(overrides)), str)


def test_canonicalize_is_round_trip_stable_and_rejects_non_finite():
    obj = {"b": [1, {"z": 1, "a": (2, 3)}], "a": 0.1}
    assert canonical_json(canonicalize(obj)) == canonical_json(canonicalize(json.loads(pretty_json(obj))))
    assert list(canonicalize(obj)) == ["a", "b"]
    try:
        canonicalize({"x": float("nan")})
    except ValueError:
        return
    raise AssertionError("non-finite value accepted")


def test_scoring_axes_keep_documentation_validity_and_robustness_separate():
    ax = scoring.score_axes(["sourced"] + ["illustrative"] * 8, "robust")
    assert ax["scientific_validity"] == scoring.VALIDITY_NOT_ASSESSED
    assert ax["scientific_validity_note"] == scoring.CONDITIONAL_NOTE
    assert ax["evidence_documentation"]["score"] == scoring.evidence_score(["sourced"] + ["illustrative"] * 8)
    assert ax["candidate_robustness"]["classification"] == "robust"


def test_better_documented_fragile_candidate_does_not_outrank_a_robust_one():
    rows = [("A", 0.9, "evidence_sensitive"), ("B", 0.1, "robust"), ("C", 0.5, "refuted"), ("D", 0.4, "inconclusive")]
    order = [r[0] for r in sorted(rows, key=lambda r: scoring.rank_key(r[1], r[2], r[0]))]
    assert order == ["B", "D", "A", "C"]
    # within a tier: higher total first
    assert scoring.rank_key(0.3, "robust", "x") < scoring.rank_key(0.2, "robust", "x")
    assert scoring.rank_key(0.3, "robust", "a") < scoring.rank_key(0.3, "robust", "b")  # ties: by id


def test_REAL_f_record_documents_no_range_so_none_is_invented():
    """REAL evidence (OWID release f.global.2024), separate from the synthetic fixtures. The release documents
    uncertainty 'unknown', so evidence-sensitive evaluation must report insufficient evidence, not make up variation.
    Model is the synthetic linear fixture; the evidence is real. Canonical value uses the declared g->kg factor 1e-3
    (hand-applied here; the production resolver does this conversion)."""
    d = json.loads((REPO / "data/releases/owid-electricity-carbon-intensity/1.0.0/dataset.json").read_text())
    rec = next(r for r in d["records"] if r["evidence"]["record_id"] == "f.global.2024")["evidence"]
    assert rec["uncertainty"] == {"kind": "unknown"} and rec["unit"] == "gCO2e/kWh"
    f_canon = rec["value"] * 1e-3
    real = record("f.global.2024", f_canon, source=rec["source"]["source_id"], geography="World", period="2024")
    assert real.range_canonical is None
    s = build_scenario_set(resolution("q", [real], select=real.evidence_id), UNIT)
    assert s.ranges == [] and len(s.scenarios) == 1 and s.source_keys == ["owid-energy-data@f8de88a"]
    ev = run(
        [s], base={"q": f_canon}, perturb_vars=["q"], mk=lambda over: make({"q": over.get("q", f_canon) / f_canon})
    )
    assert ev.decision.classification == "evidence_insufficient"
    assert ev.decision.numerical_class == "robust"
