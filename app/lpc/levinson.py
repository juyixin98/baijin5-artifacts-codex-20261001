"""Levinson-Durbin recursion with stability diagnostics.

Solves the Yule-Walker system R a = e for the predictor coefficients
a = [1, a1, ..., a_order] given the autocorrelation vector r[0..order].

Diagnostics are emitted (never silently swallowed) when:
  - the frame has zero energy (defined behaviour: a = [1, 0, ..., 0]);
  - a reflection coefficient reaches or exceeds magnitude 1 (the
    synthesis filter 1/A(z) would be unstable);
  - the prediction error energy becomes non-positive or non-finite
    (numerically singular autocorrelation, e.g. order too high for the
    signal content).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

#: diagnostic categories (stable strings, asserted by tests)
DIAG_ZERO_ENERGY = "zero_energy_frame"
DIAG_UNSTABLE_REFLECTION = "unstable_reflection_coefficient"
DIAG_SINGULAR_AUTOCORR = "singular_autocorrelation"

_STABILITY_MARGIN = 1.0 - 1e-12


@dataclass(frozen=True)
class LevinsonResult:
    """Outcome of one Levinson-Durbin solve."""

    lpc: np.ndarray  # shape (order+1,), lpc[0] == 1
    reflection_coeffs: np.ndarray  # shape (order,) — truncated at breakdown
    error_energy: float  # final prediction error energy E
    gain: float  # sqrt(error_energy)
    zero_energy: bool
    stable: bool
    diagnostics: tuple[str, ...] = field(default=())

    @property
    def max_abs_reflection(self) -> float:
        if self.reflection_coeffs.size == 0:
            return 0.0
        return float(np.max(np.abs(self.reflection_coeffs)))


def levinson_durbin(r: np.ndarray, order: int) -> LevinsonResult:
    """Run the Levinson-Durbin recursion on autocorrelation r[0..order].

    The recursion never raises on degenerate input; degeneracies are
    reported through the result's diagnostics instead.
    """
    r = np.asarray(r, dtype=np.float64)
    if r.shape[0] < order + 1:
        raise ValueError(f"need r[0..{order}], got {r.shape[0]} values")
    if order < 1:
        raise ValueError("order must be >= 1")

    diagnostics: list[str] = []

    # --- zero-energy frame: defined behaviour --------------------------
    if r[0] <= 0.0:
        diagnostics.append(DIAG_ZERO_ENERGY)
        return LevinsonResult(
            lpc=np.concatenate(([1.0], np.zeros(order))),
            reflection_coeffs=np.zeros(order),
            error_energy=0.0,
            gain=0.0,
            zero_energy=True,
            stable=True,
            diagnostics=tuple(diagnostics),
        )

    a = np.zeros(order + 1)
    a[0] = 1.0
    reflections = np.zeros(order)
    error_energy = float(r[0])
    stable = True
    completed = order

    for m in range(1, order + 1):
        # k_m = -(r[m] + sum_{i=1}^{m-1} a[i] r[m-i]) / E_{m-1}
        acc = r[m] + float(np.dot(a[1:m], r[m - 1 : 0 : -1]))
        k = -acc / error_energy
        reflections[m - 1] = k

        if abs(k) >= _STABILITY_MARGIN:
            stable = False
            if DIAG_UNSTABLE_REFLECTION not in diagnostics:
                diagnostics.append(DIAG_UNSTABLE_REFLECTION)

        # step-up: a_m[i] = a_{m-1}[i] + k * a_{m-1}[m-i]
        prev = a[1:m].copy()
        a[1:m] = prev + k * prev[::-1]
        a[m] = k

        error_energy *= 1.0 - k * k
        if not np.isfinite(error_energy) or error_energy <= 0.0:
            # Autocorrelation matrix is (numerically) singular: further
            # recursion steps are meaningless. Keep the coefficients
            # computed so far, zero the rest, and flag it.
            diagnostics.append(DIAG_SINGULAR_AUTOCORR)
            a[m + 1 :] = 0.0
            reflections[m:] = 0.0
            error_energy = max(error_energy, 0.0) if np.isfinite(error_energy) else 0.0
            stable = False
            completed = m
            break

    return LevinsonResult(
        lpc=a,
        reflection_coeffs=reflections[:completed],
        error_energy=error_energy,
        gain=float(np.sqrt(error_energy)),
        zero_energy=False,
        stable=stable,
        diagnostics=tuple(diagnostics),
    )
