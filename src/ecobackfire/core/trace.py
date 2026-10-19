"""Calculation trace: ordered, human-readable record of each equation applied."""

from __future__ import annotations


class Trace:
    def __init__(self) -> None:
        self.steps: list[dict[str, object]] = []

    def step(
        self,
        equation: str,
        formula: str,
        inputs: dict[str, float],
        output: dict[str, float],
        unit: str = "",
    ) -> None:
        self.steps.append(
            {
                "step": len(self.steps) + 1,
                "equation": equation,
                "formula": formula,
                "inputs": dict(inputs),
                "output": dict(output),
                "unit": unit,
            }
        )
