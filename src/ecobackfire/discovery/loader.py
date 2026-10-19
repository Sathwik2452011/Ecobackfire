"""Load a DiscoverySpec from TOML (mirrors api.load_config's error handling)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import ValidationError

from ecobackfire.contracts.discovery import DiscoverySpec
from ecobackfire.errors import ConfigError


def load_discovery_spec(path: str | Path) -> DiscoverySpec:
    p = Path(path)
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"discovery spec not found: {p}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {p}: {exc}") from exc
    try:
        return DiscoverySpec.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid discovery spec {p}:\n{exc}") from exc
