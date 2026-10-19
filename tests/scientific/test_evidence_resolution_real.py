"""Resolution against the REAL shipped release (OWID/Ember grid carbon intensity, 2024) and E2 regression.

Only variable `f` has real evidence; every other input of the rebound model stays illustrative. Results here are
model-derived under those illustrative inputs, not findings about any real device or country.
"""

from __future__ import annotations

import pytest
from conftest import EXAMPLES, ROOT, make_config

from ecobackfire import api
from ecobackfire.contracts.resolution import EvidenceRequest
from ecobackfire.datasets.registry import DatasetRegistry
from ecobackfire.datasets.resolution import resolve_parameter
from ecobackfire.errors import MissingEvidenceError
from ecobackfire.experiments.runner import run_experiment
from ecobackfire.models.registry import get_model
from ecobackfire.verification.reference import rebound_analytical as ana

pytestmark = pytest.mark.scientific
DS = "owid-electricity-carbon-intensity"
SPEC = get_model("efficiency_rebound@1.0.0").spec
EXPECTED_G = {"IN": 705.4, "US": 383.78, "DE": 336.38, "FR": 40.48, "GLOBAL": 471.08}  # values in the release


@pytest.fixture(scope="module")
def reg() -> DatasetRegistry:
    return DatasetRegistry.from_directory(ROOT / "data" / "releases", strict=True)


def _req(**kw: object) -> EvidenceRequest:
    return EvidenceRequest(**{"dataset_id": DS, "version": "1.0.0", "geography": "IN", "period": "2024", **kw})  # type: ignore[arg-type]


@pytest.mark.parametrize("geo", sorted(EXPECTED_G))
def test_each_geography_resolves_to_its_own_record_with_recorded_conversion(reg: DatasetRegistry, geo: str) -> None:
    res = resolve_parameter(reg, SPEC, "f", _req(geography=geo))
    s = res.selected
    assert res.state == "resolved" and s is not None and s.geography_match == "exact"
    assert s.value == EXPECTED_G[geo] and s.unit == "gCO2e/kWh"
    assert s.conversion.canonical_value == pytest.approx(EXPECTED_G[geo] / 1000, rel=1e-12)
    assert s.status == "sourced_unreviewed" and not s.evidence_backed
    assert s.source is not None and s.source.url and "f8de88a" in s.source.source_id


def test_no_silent_world_average_for_an_unlisted_country(reg: DatasetRegistry) -> None:
    strict = resolve_parameter(reg, SPEC, "f", _req(geography="BR"))
    assert strict.state == "missing"
    loose = resolve_parameter(reg, SPEC, "f", _req(geography="BR", geography_fallback=True))
    assert loose.state == "resolved" and loose.selected.geography_match == "global"  # type: ignore[union-attr]
    assert any("geography fallback" in w for w in loose.warnings)


def test_other_year_and_other_inputs_have_no_evidence(reg: DatasetRegistry) -> None:
    assert resolve_parameter(reg, SPEC, "f", _req(period="2023")).state == "missing"
    for vid in ("E0", "eta", "phi", "epsilon", "EM0", "EM1", "L", "s_max"):
        r = resolve_parameter(reg, SPEC, vid, _req())
        assert r.state == "missing" and r.selected is None, vid


def test_run_matches_the_independent_closed_form_with_evidence_backed_f() -> None:
    cfg = api.load_config(EXAMPLES.parent / "evidence" / "e2_eps_f_from_evidence.toml")
    reg = DatasetRegistry.from_directory(ROOT / "data" / "releases", strict=True)
    res = run_experiment(cfg, registry=reg)
    base = {k: v.value for k, v in res.base_inputs.items()}
    assert base["f"] == pytest.approx(0.7054, rel=1e-12)
    expected = ana.predicted_epsilon_roots(base, res.options)
    assert len(expected) == 1 and res.breakeven is not None and len(res.breakeven.roots) == 1
    assert res.breakeven.roots[0].value == pytest.approx(expected[0], rel=1e-9)
    assert res.weakest_provenance.value == "illustrative"


def test_a_different_real_geography_changes_the_model_answer_visibly() -> None:
    cfg = api.load_config(EXAMPLES.parent / "evidence" / "e2_eps_f_from_evidence.toml")
    reg = DatasetRegistry.from_directory(ROOT / "data" / "releases", strict=True)
    roots = {}
    for geo in ("IN", "US", "DE"):
        c = make_config(cfg, evidence_requests={"f": _req(geography=geo).model_dump(mode="json")})
        r = run_experiment(c, registry=reg)
        base = {k: v.value for k, v in r.base_inputs.items()}
        assert r.breakeven.roots[0].value == pytest.approx(ana.predicted_epsilon_roots(base, r.options)[0], rel=1e-9)  # type: ignore[union-attr]
        roots[geo] = r.breakeven.roots[0].value  # type: ignore[union-attr]
    assert len(set(round(v, 6) for v in roots.values())) == 3


def test_france_has_no_break_even_and_that_is_reported_not_forced() -> None:
    cfg = api.load_config(EXAMPLES.parent / "evidence" / "e2_eps_f_from_evidence.toml")
    reg = DatasetRegistry.from_directory(ROOT / "data" / "releases", strict=True)
    r = run_experiment(
        make_config(cfg, evidence_requests={"f": _req(geography="FR").model_dump(mode="json")}), registry=reg
    )
    base = {k: v.value for k, v in r.base_inputs.items()}
    assert ana.predicted_epsilon_roots(base, r.options) == []
    assert r.breakeven is not None and r.breakeven.roots == []


def test_missing_evidence_stops_a_run_even_though_illustrative_values_exist(reg: DatasetRegistry) -> None:
    cfg = api.load_config(EXAMPLES.parent / "evidence" / "e2_eps_f_from_evidence.toml")
    bad = make_config(cfg, evidence_requests={"f": _req(period="1999").model_dump(mode="json")})
    with pytest.raises(MissingEvidenceError, match="temporal_mismatch"):
        run_experiment(bad, registry=reg)


def test_evidence_example_reproduces_byte_identically(reg: DatasetRegistry, tmp_path) -> None:  # type: ignore[no-untyped-def]
    cfg = api.load_config(EXAMPLES.parent / "evidence" / "e2_eps_f_from_evidence.toml")
    rd = api.execute(cfg, tmp_path, registry=reg)
    out = api.reproduce(rd / "manifest.json", registry=reg)
    assert out.ok and out.exact
