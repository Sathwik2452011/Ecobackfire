"""Model protocol. Concrete models live in ecobackfire.models (layer 2)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from ecobackfire.contracts.common import SystemBoundary
from ecobackfire.contracts.model import EnvironmentalModel
from ecobackfire.core.trace import Trace


class Model(Protocol):
    spec: EnvironmentalModel

    def resolve_options(self, options: Mapping[str, str]) -> dict[str, str]: ...

    def system_boundary(self, options: Mapping[str, str]) -> SystemBoundary: ...

    def required_inputs(self, options: Mapping[str, str]) -> list[str]: ...

    def evaluate(
        self,
        inputs: Mapping[str, float],
        options: Mapping[str, str],
        trace: Trace | None = None,
    ) -> dict[str, float]:
        """Pure, deterministic. Inputs are floats in the spec's canonical units."""
        ...
