from __future__ import annotations

from pathlib import Path

import pytest

from ecobackfire import api
from ecobackfire.contracts.experiment import Binding, ExperimentConfiguration

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "rebound_breakeven"


@pytest.fixture(scope="session")
def e2_config() -> ExperimentConfiguration:
    return api.load_config(EXAMPLES / "e2_breakeven_eps.toml")


def make_config(base: ExperimentConfiguration, **changes: object) -> ExperimentConfiguration:
    """Copy of ``base`` with top-level fields replaced (re-validated)."""
    data = base.model_dump(mode="json")
    data.update(changes)
    return ExperimentConfiguration.model_validate(data)


def with_binding(base: ExperimentConfiguration, vid: str, **fields: object) -> ExperimentConfiguration:
    bindings = {k: v.model_dump(mode="json") for k, v in base.bindings.items()}
    bindings[vid] = {**bindings[vid], **fields}
    Binding.model_validate(bindings[vid])  # fail early with a clear error if the edit is invalid
    return make_config(base, bindings=bindings)


def physical_inputs(**kw: float) -> dict[str, float]:
    """Canonical-unit inputs with fE0L = 5000 so r = EM1/5000 when EM0 = 0."""
    d = {
        "E0": 1000.0,
        "f": 0.5,
        "L": 10.0,
        "EM0": 0.0,
        "EM1": 0.0,
        "eta": 0.2,
        "phi": 1.0,
        "epsilon": 0.0,
        "s_max": 1.5,
    }
    d.update(kw)
    return d
