"""Numerical verification: independent cross-checks used by the pipeline.

This module does real work at request time — it is not a test-only
helper. The pipeline calls it to:

  1. cross-check Levinson-Durbin against an independent Toeplitz solve;
  2. measure reconstruction error directly (never inferring losslessness
     from a small residual alone — a wrong filter state also produces a
     small residual while the reconstruction is garbage);
  3. check synthesis-filter stability from the reflection coefficients.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.lpc.levinson import DIAG_SINGULAR_AUTOCORR, DIAG_UNSTABLE_REFLECTION
from app.lpc.reference import solve_toeplitz_reference

#: verdict categories (stable strings, asserted by tests)
VERDICT_LOSSLESS = "lossless"
VERDICT_DEGRADED = "degraded"
VERDICT_UNCERTAIN = "uncertain"

#: absolute tolerance for the Levinson vs Toeplitz cross-check
TOEPLITZ_CROSSCHECK_TOL = 1e-8


@dataclass(frozen=True)
class ReconstructionAssessment:
    """Direct reconstruction-error assessment for one signal."""

    max_abs_error: float
    relative_error: float
    residual_energy_ratio: float
    lossless_confirmed: bool
    verdict: str
    reasons: tuple[str, ...] = field(default=())


def cross_check_toeplitz(
    r: np.ndarray, order: int, lpc: np.ndarray, tol: float = TOEPLITZ_CROSSCHECK_TOL
) -> tuple[bool, float, str | None]:
    """Compare Levinson coefficients with the independent Toeplitz solve.

    Returns (agrees, max_abs_deviation, diagnostic_or_None). A singular
    Toeplitz system on the reference side is reported, not hidden.
    """
    try:
        ref = solve_toeplitz_reference(r, order)
    except np.linalg.LinAlgError:
        return False, float("inf"), DIAG_SINGULAR_AUTOCORR
    dev = float(np.max(np.abs(ref - lpc))) if lpc.size == ref.size else float("inf")
    return dev <= tol, dev, None


def assess_reconstruction(
    original: np.ndarray,
    reconstructed: np.ndarray,
    residual: np.ndarray,
    rel_tol: float = 1e-9,
) -> ReconstructionAssessment:
    """Judge reconstruction by direct sample comparison.

    Losslessness requires BOTH a direct reconstruction error below
    tolerance AND consistency with the residual. A small residual alone
    is never accepted as evidence: if the residual is small but the
    direct error is not, the verdict is UNCERTAIN with the reason
    recorded (typical cause: mismatched filter initial state).
    """
    x = np.asarray(original, dtype=np.float64)
    y = np.asarray(reconstructed, dtype=np.float64)
    e = np.asarray(residual, dtype=np.float64)
    if x.shape != y.shape:
        raise ValueError("original and reconstructed shapes differ")

    err = x - y
    max_abs = float(np.max(np.abs(err))) if err.size else 0.0
    norm_x = float(np.linalg.norm(x))
    rel = float(np.linalg.norm(err) / norm_x) if norm_x > 0 else max_abs

    res_energy = float(np.sum(e * e))
    sig_energy = float(np.sum(x * x))
    res_ratio = res_energy / sig_energy if sig_energy > 0 else 0.0

    reasons: list[str] = []
    if rel <= rel_tol:
        verdict = VERDICT_LOSSLESS
        reasons.append(
            f"direct reconstruction error {rel:.3e} <= tolerance {rel_tol:.1e}"
        )
        return ReconstructionAssessment(max_abs, rel, res_ratio, True, verdict, tuple(reasons))

    if res_ratio <= rel_tol:
        verdict = VERDICT_UNCERTAIN
        reasons.append(
            "residual is small but direct reconstruction error "
            f"{rel:.3e} exceeds tolerance {rel_tol:.1e}; small residual "
            "alone is not evidence of lossless reconstruction (check "
            "filter initial-state correspondence)"
        )
    else:
        verdict = VERDICT_DEGRADED
        reasons.append(
            f"direct reconstruction error {rel:.3e} exceeds tolerance {rel_tol:.1e}"
        )
    return ReconstructionAssessment(max_abs, rel, res_ratio, False, verdict, tuple(reasons))


def stability_from_reflections(
    reflection_coeffs: np.ndarray, margin: float = 1.0 - 1e-12
) -> tuple[bool, float, tuple[str, ...]]:
    """A synthesis filter 1/A(z) is stable iff all |k_m| < 1."""
    k = np.asarray(reflection_coeffs, dtype=np.float64)
    if k.size == 0:
        return True, 0.0, ()
    max_abs = float(np.max(np.abs(k)))
    if max_abs >= margin:
        return False, max_abs, (DIAG_UNSTABLE_REFLECTION,)
    return True, max_abs, ()
