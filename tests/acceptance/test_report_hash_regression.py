"""Regression: `verify` followed by `reproduce` reported a report.md hash mismatch (key ordering after JSON round trip).

Needs the full stack (pint). The pure counterpart of this regression lives in
tests/software/test_evidence_manifest_report.py::test_report_hash_regression_key_order_after_json_round_trip.
"""

from __future__ import annotations

import pytest
from conftest import EXAMPLES

from ecobackfire import api


@pytest.mark.parametrize("cfg", ["e2_breakeven_eps", "e1_compare", "e7_oat_breakeven_eps"])
def test_verify_then_reproduce_has_no_report_hash_mismatch(cfg, tmp_path):
    rd = api.execute(api.load_config(EXAMPLES / f"{cfg}.toml"), tmp_path)
    before = (rd / "report.md").read_bytes()
    api.reverify(rd)  # reloads result.json (key-sorted) and rewrites the artifacts + manifest
    assert (rd / "report.md").read_bytes() == before  # same bytes: rendering no longer depends on dict insertion order
    out = api.reproduce(rd / "manifest.json")
    assert out.ok and out.exact, out.details
    assert not any("report.md" in d for d in out.details)


def test_trace_outputs_are_rendered_in_sorted_key_order(tmp_path):
    rd = api.execute(api.load_config(EXAMPLES / "e2_breakeven_eps.toml"), tmp_path)
    assert "dC_normalized=" in (rd / "report.md").read_text()
    line = next(x for x in (rd / "report.md").read_text().splitlines() if x.startswith("- EQ6"))
    assert line.index("dC_normalized=") < line.index("r=0.05")
