"""The labeling gate and mutation tests: passing unit tests must never imply 'verified'."""

import pytest
from conftest import make_config

from ecobackfire.contracts.verification import CheckResult
from ecobackfire.experiments.runner import run_experiment
from ecobackfire.models.registry import get_model
from ecobackfire.verification import checks as checks_mod
from ecobackfire.verification.labeling import MUST_PASS_FAMILIES, label_from_checks
from ecobackfire.verification.suite import verify


def chk(cid, family, status, required=True):
    return CheckResult(
        check_id=cid, family=family, required=required, status=status, summary=f"{cid} {status}", details={}
    )


def all_passed():
    return [chk(f"c_{f}", f, "passed") for f in sorted(MUST_PASS_FAMILIES)]


# ------------------------------------------------------------ pure labeling function (synthetic inputs)
def test_gate_all_passed_no_limits_is_verified_internal_but_only_with_a_claim():
    assert label_from_checks(all_passed(), has_claim=True)[0] == "verified_internal"
    assert label_from_checks(all_passed(), has_claim=False)[0] == "not_applicable"


def test_gate_failed_falsification_refutes():
    cs = [*all_passed(), chk("F1", "falsification_perturbation", "failed")]
    assert label_from_checks(cs, True)[0] == "refuted"


def test_gate_failed_recomputation_is_inconclusive_not_verified():
    cs = [c for c in all_passed() if c.family != "independent_recomputation"] + [
        chk("V2", "independent_recomputation", "failed")
    ]
    assert label_from_checks(cs, True)[0] == "inconclusive"


def test_gate_skipped_required_check_blocks_verification():
    cs = [c for c in all_passed() if c.family != "benchmarks"] + [chk("V4", "benchmarks", "skipped")]
    assert label_from_checks(cs, True)[0] == "inconclusive"


def test_gate_missing_family_blocks_verification():
    cs = [c for c in all_passed() if c.family != "stability"]
    label, why = label_from_checks(cs, True)
    assert label == "inconclusive" and "stability" in " ".join(why)


def test_gate_limits_claim_gives_conditional():
    cs = [*all_passed(), chk("F7", "evidence_gate", "limits_claim")]
    assert label_from_checks(cs, True)[0] == "conditional"


def test_gate_failure_outranks_limits():
    cs = [*all_passed(), chk("F7", "evidence_gate", "limits_claim"), chk("V1", "closed_form", "failed")]
    assert label_from_checks(cs, True)[0] == "inconclusive"


def test_gate_cannot_emit_human_only_labels():
    for cs in (all_passed(), []):
        assert label_from_checks(cs, True)[0] not in ("established", "independently_reproduced")


# ------------------------------------------------------------ the real pipeline
def test_real_experiment_is_conditional_not_verified(e2_config):
    res = run_experiment(e2_config)
    rep = verify(e2_config, res)
    assert rep.recommended_label == "conditional"  # illustrative inputs + model-form + corner limits
    # the stored label equals the label recomputed from the stored checks
    assert label_from_checks(rep.checks, has_claim=rep.claim is not None)[0] == rep.recommended_label
    assert not any(c.status == "failed" for c in rep.checks)
    assert any("Prior-art" in u for u in rep.unresolved_uncertainties)


def test_missing_benchmarks_prevent_verification(e2_config, monkeypatch):
    monkeypatch.setattr(checks_mod, "_find_benchmarks", lambda explicit: None)
    res = run_experiment(e2_config)
    rep = verify(e2_config, res)
    v4 = next(c for c in rep.checks if c.check_id.startswith("V4"))
    assert v4.status == "skipped"
    assert rep.recommended_label == "inconclusive"


def test_gate_can_reach_verified_internal_only_without_illustrative_inputs_or_limits(e2_config):
    """Reachability test: non-illustrative inputs, narrow uncertainty, and alternatives that keep the reversal."""
    data = e2_config.model_dump(mode="json")
    for _vid, b in data["bindings"].items():
        b["provenance_class"], b["note"] = "user_supplied", None
        v = b["value"]
        if b["uncertainty"]["kind"] == "interval":
            b["uncertainty"] = {"kind": "interval", "low": v * 0.97, "high": v * 1.03}
    data["verification"]["alternative_s_max"] = [1.5, 2.0, 3.0]
    cfg = make_config(e2_config, **{k: data[k] for k in ("bindings", "verification")})
    res = run_experiment(cfg)
    rep = verify(cfg, res)
    statuses = {c.check_id: c.status for c in rep.checks}
    assert rep.recommended_label == "verified_internal", (rep.label_justification, statuses)
    # same experiment with illustrative provenance must NOT reach it
    assert verify(e2_config, run_experiment(e2_config)).recommended_label != "verified_internal"


# ------------------------------------------------------------ mutation tests
class Mutant:
    """Delegates to the real model but injects a specific bug."""

    def __init__(self, pre=None, post=None, opts_fn=None):
        self.real = get_model("efficiency_rebound@1.0.0")
        self.spec = self.real.spec
        self.pre, self.post, self.opts_fn = pre, post, opts_fn

    def resolve_options(self, o):
        return self.real.resolve_options(o)

    def system_boundary(self, o):
        return self.real.system_boundary(o)

    def required_inputs(self, o):
        return self.real.required_inputs(o)

    def evaluate(self, inputs, options, trace=None):
        inp = self.pre(dict(inputs)) if self.pre else dict(inputs)
        opts = self.opts_fn(dict(options)) if self.opts_fn else options
        out = self.real.evaluate(inp, opts, trace)
        return self.post(dict(out), inputs) if self.post else out


def _offset(out, inputs):
    out["dC"] += 5.0
    return out


def _flip(out, inputs):
    out["dC"] = -out["dC"]
    return out


MUTANTS = {
    "additive_offset_in_dC": Mutant(post=_offset),
    "sign_flip_in_dC": Mutant(post=_flip),
    "elasticity_scaled_by_0.9": Mutant(pre=lambda i: {**i, "epsilon": i["epsilon"] * 0.9}),
    "ignores_phi": Mutant(pre=lambda i: {**i, "phi": 1.0}),
    "ignores_boundary_variant": Mutant(opts_fn=lambda o: {**o, "boundary_variant": "B1"}),
}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_seeded_model_bugs_are_never_labeled_verified_or_conditional(e2_config, name):
    mut = MUTANTS[name]
    res = run_experiment(e2_config, model=mut)
    rep = verify(e2_config, res, model=mut)
    failed = [c for c in rep.checks if c.status == "failed"]
    assert failed, f"{name}: no check detected the bug"
    assert {c.family for c in failed} & {"closed_form", "independent_recomputation", "benchmarks"}
    assert rep.recommended_label in ("refuted", "inconclusive")


def test_hidden_narrow_sign_change_is_caught_only_by_dense_counterexample_search(e2_config):
    """A dip narrower than the scan grid hides a second break-even; F2 (offset dense sampling) must find it."""

    def dip(out, inputs):
        if 2.0035 <= inputs["epsilon"] <= 2.0065:
            out["dC"] = -1000.0
        return out

    mut = Mutant(post=dip)
    res = run_experiment(e2_config, model=mut)
    assert len(res.breakeven.roots) == 1  # the scan grid misses the dip, exactly as the limitation says
    rep = verify(e2_config, res, model=mut)
    f2 = next(c for c in rep.checks if c.check_id.startswith("F2"))
    assert f2.status == "failed" and f2.details["unexplained"]
    assert rep.recommended_label == "refuted"
