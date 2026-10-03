"""Levinson-Durbin recursion with explicit stability diagnostics.

Solves R a = -r for the predictor coefficients given the autocorrelation
sequence r[0..p]. The recursion is monitored at every step:

* r[0] <= zero-energy eps  -> zero-energy frame, a *defined* degenerate
  result (coefficients [1, 0, ..., 0], zero gain), reported as info.
* prediction-error energy E collapses to ~0 before step m -> the
  autocorrelation matrix is numerically singular (typically: order too
  high for the signal's effective rank). Reported as an error and the
  recursion stops; remaining coefficients stay zero.
* |reflection coefficient| >= 1 - tol -> the synthesis filter 1/A(z) is not
  guaranteed stable. Reported as an error.
* |k| close to (but below) 1 -> thin stability margin, reported as a
  warning (uncertain conclusion, kept separate from hard failures).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.config import (
    ERROR_ENERGY_EPS,
    MARGINAL_REFLECTION,
    REFLECTION_STABILITY_TOL,
    ZERO_ENERGY_EPS,
)
from app.lpc.types import Diagnostic

STAGE = "levinson-durbin"


@dataclass
class LevinsonResult:
    coefficients: np.ndarray  # length order+1, leading 1.0
    reflection_coefficients: list[float]
    prediction_error_energy: float
    gain: float
    stable: bool
    completed_order: int
    diagnostics: list[Diagnostic] = field(default_factory=list)


def levinson_durbin(
    r: np.ndarray,
    order: int,
    *,
    zero_energy_eps: float = ZERO_ENERGY_EPS,
    error_energy_eps: float = ERROR_ENERGY_EPS,
    reflection_tol: float = REFLECTION_STABILITY_TOL,
    marginal_reflection: float = MARGINAL_REFLECTION,
) -> LevinsonResult:
    r = np.asarray(r, dtype=float)
    if r.size < order + 1:
        raise ValueError(f"need r[0..{order}], got {r.size} values")

    diagnostics: list[Diagnostic] = []
    coefficients = np.zeros(order + 1)
    coefficients[0] = 1.0
    reflections: list[float] = []

    r0 = float(r[0])
    if not np.isfinite(r0) or r0 <= zero_energy_eps:
        diagnostics.append(
            Diagnostic(
                code="ZERO_ENERGY_FRAME",
                severity="info",
                stage=STAGE,
                message=(
                    "Frame energy r[0] is zero or below eps; defined behaviour: "
                    "coefficients [1, 0, ..., 0], zero gain, zero residual."
                ),
            )
        )
        return LevinsonResult(
            coefficients=coefficients,
            reflection_coefficients=reflections,
            prediction_error_energy=0.0,
            gain=0.0,
            stable=True,
            completed_order=0,
            diagnostics=diagnostics,
        )

    error_energy = r0
    stable = True
    completed_order = 0

    for m in range(1, order + 1):
        if error_energy <= error_energy_eps:
            diagnostics.append(
                Diagnostic(
                    code="PREDICTION_ERROR_ENERGY_COLLAPSED",
                    severity="error",
                    stage=STAGE,
                    message=(
                        f"Prediction-error energy collapsed to {error_energy:.3e} "
                        f"before step {m}: the autocorrelation matrix is numerically "
                        f"singular, order {order} is too high for this frame. "
                        f"Recursion stopped at order {completed_order}."
                    ),
                )
            )
            stable = False
            break

        # lambda_m = sum_{j=0}^{m-1} a_j * r[m-j]
        lam = float(np.dot(coefficients[:m], r[m:0:-1]))
        k = -lam / error_energy
        reflections.append(k)

        abs_k = abs(k)
        if abs_k >= 1.0 - reflection_tol:
            diagnostics.append(
                Diagnostic(
                    code="REFLECTION_COEFFICIENT_UNSTABLE",
                    severity="error",
                    stage=STAGE,
                    message=(
                        f"Reflection coefficient k_{m} = {k:.6f} violates |k| < 1; "
                        "synthesis filter 1/A(z) is not guaranteed stable."
                    ),
                )
            )
            stable = False
        elif abs_k >= marginal_reflection:
            diagnostics.append(
                Diagnostic(
                    code="REFLECTION_COEFFICIENT_MARGINAL",
                    severity="warning",
                    stage=STAGE,
                    message=(
                        f"Reflection coefficient k_{m} = {k:.6f} is close to the "
                        "unit circle; stability margin is thin."
                    ),
                )
            )

        # Order update: a_new[j] = a[j] + k * a[m-j] for j = 0..m
        # (with the convention a[m] = 0 before this step, so a_new[0] = 1
        # and a_new[m] = k).
        a_ext = coefficients[: m + 1]
        coefficients[: m + 1] = a_ext + k * a_ext[::-1]

        error_energy *= 1.0 - k * k
        completed_order = m

    gain = float(np.sqrt(error_energy)) if error_energy > 0.0 else 0.0
    return LevinsonResult(
        coefficients=coefficients,
        reflection_coefficients=reflections,
        prediction_error_energy=float(max(error_energy, 0.0)),
        gain=gain,
        stable=stable,
        completed_order=completed_order,
        diagnostics=diagnostics,
    )
