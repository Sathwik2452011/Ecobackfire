"""Model registry keyed by 'model_id@exact.version'. The runner depends on the protocol only."""

from __future__ import annotations

from ecobackfire.core.model import Model
from ecobackfire.errors import ConfigError
from ecobackfire.models.efficiency_rebound import EfficiencyReboundModel

_REGISTRY: dict[str, Model] = {}


def _register(model: Model) -> None:
    _REGISTRY[model.spec.ref] = model


_register(EfficiencyReboundModel())


def get_model(ref: str) -> Model:
    try:
        return _REGISTRY[ref]
    except KeyError:
        raise ConfigError(f"unknown model {ref!r}; available: {sorted(_REGISTRY)}") from None


def list_models() -> list[str]:
    return sorted(_REGISTRY)
