"""Shared result/diagnostic types for the LPC pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Severities: "info" (defined behaviour, e.g. zero-energy frame),
# "warning" (uncertain conclusion), "error" (failure reason).
SEVERITIES = ("info", "warning", "error")


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: str
    stage: str
    message: str


@dataclass
class AnalysisResult:
    """Outcome of analysing one frame."""

    order: int
    window: str
    frame_energy: float
    coefficients: np.ndarray  # length order+1, leading coefficient 1.0
    reflection_coefficients: list[float]
    prediction_error_energy: float
    gain: float
    residual: np.ndarray
    residual_energy: float
    analysis_final_state: np.ndarray
    max_pole_magnitude: float | None
    stable: bool
    completed_order: int
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def errors(self) -> list[str]:
        return [d.message for d in self.diagnostics if d.severity == "error"]

    @property
    def warnings(self) -> list[str]:
        return [d.message for d in self.diagnostics if d.severity == "warning"]
