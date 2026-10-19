"""Load a ResearchMission from TOML (mirrors api.load_config / discovery.loader error handling)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import ValidationError

from ecobackfire.contracts.mission import ResearchMission
from ecobackfire.errors import ConfigError


def load_mission(path: str | Path) -> ResearchMission:
    p = Path(path)
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"mission file not found: {p}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {p}: {exc}") from exc
    try:
        return ResearchMission.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid mission {p}:\n{exc}") from exc
