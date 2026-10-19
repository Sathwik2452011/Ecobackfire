"""The committed expected outputs (quoted in docs/phase-2-vertical-slice.md) must match a fresh run."""

import json

import pytest
from conftest import EXAMPLES

from ecobackfire import api
from ecobackfire.reporting.manifest import json_close

EXPECTED = EXAMPLES / "expected"


@pytest.mark.parametrize("name", ["e1_compare", "e2_breakeven_eps", "e3_breakeven_f", "e7_oat_breakeven_eps"])
def test_fresh_run_matches_committed_expected_outputs(name, tmp_path):
    cfg_path = EXAMPLES / f"{name}.toml"
    rd = api.execute(api.load_config(cfg_path), tmp_path, source_config_path=str(cfg_path))
    for fname in ("result.json", "verification.json"):
        exp = EXPECTED / name / fname
        if not exp.exists():
            continue
        diffs = json_close(json.loads(exp.read_text()), json.loads((rd / fname).read_text()), 1e-9, 1e-12)
        assert not diffs, (fname, diffs[:3])
    assert (EXPECTED / name / "report.md").read_text(encoding="utf-8").splitlines()[0] == (rd / "report.md").read_text(
        encoding="utf-8"
    ).splitlines()[0]
