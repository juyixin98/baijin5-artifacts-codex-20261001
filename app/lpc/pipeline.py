"""Frame pipeline: window -> autocorrelation -> Levinson-Durbin -> filters.

Also hosts the round-trip verification logic. Important contract:
reconstruction is verified by comparing the *reconstructed signal against
the original samples*; a small residual alone is never accepted as
evidence of lossless reconstruction (the residual is small by
construction of the predictor, even when the synthesis state is wrong).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.config import Settings
from app.lpc.autocorr import autocorrelation
from app.lpc.filters import AnalysisFilter, SynthesisFilter
from app.lpc.levinson import levinson_durbin
from app.lpc.toeplitz_ref import solve_normal_equations_toeplitz
from app.lpc.types import AnalysisResult, Diagnostic
from app.lpc.windowing import apply_window

PIPELINE_STAGES = (
    "window",
    "autocorrelation",
    "levinson-durbin",
    "pole-stability",
    "analysis-filter",
)


@dataclass
class ToeplitzCrosscheck:
    max_abs_deviation: float | None
    agrees: bool
    note: str


@dataclass
class AnalysisOutcome:
    result: AnalysisResult
    stages: list[str]
    toeplitz_crosscheck: ToeplitzCrosscheck | None = None


@dataclass
class SynthesisOutcome:
    samples: np.ndarray
    final_state: np.ndarray
    diagnostics: list[Diagnostic] = field(default_factory=list)


@dataclass
class ReconstructionMetrics:
    max_abs_error: float
    rms_error: float
    relative_error: float
    residual_energy_ratio: float | None
    verified: bool
    tolerance: float
    note: str


VERIFICATION_NOTE = (
    "Verified by comparing the reconstructed samples against the original "
    "frame (relative error <= tolerance). Small residual energy alone is "
    "NOT treated as evidence of lossless reconstruction."
)


def _pole_diagnostics(
    coefficients: np.ndarray, settings: Settings
) -> tuple[float | None, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    if coefficients.size < 2 or not np.any(coefficients[1:]):
        return 0.0, diagnostics  # all-pole filter degenerates to identity
    poles = np.roots(coefficients)
    if poles.size == 0:
        return None, diagnostics
    max_mag = float(np.max(np.abs(poles)))
    if max_mag > 1.0 + settings.pole_tol:
        diagnostics.append(
            Diagnostic(
                code="UNSTABLE_SYNTHESIS_FILTER",
                severity="error",
                stage="pole-stability",
                message=(
                    f"Largest pole magnitude {max_mag:.6f} lies outside the unit "
                    "circle; the synthesis filter 1/A(z) is unstable."
                ),
            )
        )
    elif max_mag > 1.0 - 1e-3:
        diagnostics.append(
            Diagnostic(
                code="POLES_NEAR_UNIT_CIRCLE",
                severity="warning",
                stage="pole-stability",
                message=(
                    f"Largest pole magnitude {max_mag:.6f} is close to the unit "
                    "circle; long reconstructions may ring."
                ),
            )
        )
    return max_mag, diagnostics


def analyze_frame(
    samples: np.ndarray,
    order: int,
    window: str,
    settings: Settings,
    *,
    verify_toeplitz: bool = False,
    analysis_filter: AnalysisFilter | None = None,
) -> AnalysisOutcome:
    """Analyse one frame. Pass ``analysis_filter`` to carry stream state."""
    x = np.asarray(samples, dtype=float)
    if order >= x.size:
        raise ValueError(
            f"order {order} must be smaller than the frame length {x.size}"
        )

    frame_energy = float(np.dot(x, x))
    windowed = apply_window(x, window)
    r = autocorrelation(windowed, order)
    ld = levinson_durbin(
        r,
        order,
        zero_energy_eps=settings.zero_energy_eps,
        error_energy_eps=settings.error_energy_eps,
        reflection_tol=settings.reflection_tol,
        marginal_reflection=settings.marginal_reflection,
    )

    max_pole, pole_diags = _pole_diagnostics(ld.coefficients, settings)

    filt = analysis_filter if analysis_filter is not None else AnalysisFilter(order)
    residual = filt.process(x, ld.coefficients)

    diagnostics = [*ld.diagnostics, *pole_diags]
    stable = ld.stable and not any(
        d.code == "UNSTABLE_SYNTHESIS_FILTER" for d in pole_diags
    )

    result = AnalysisResult(
        order=order,
        window=window,
        frame_energy=frame_energy,
        coefficients=ld.coefficients,
        reflection_coefficients=ld.reflection_coefficients,
        prediction_error_energy=ld.prediction_error_energy,
        gain=ld.gain,
        residual=residual,
        residual_energy=float(np.dot(residual, residual)),
        analysis_final_state=filt.state,
        max_pole_magnitude=max_pole,
        stable=stable,
        completed_order=ld.completed_order,
        diagnostics=diagnostics,
    )

    crosscheck = None
    if verify_toeplitz:
        crosscheck = _toeplitz_crosscheck(r, order, ld.coefficients, settings)
        if crosscheck is not None and not crosscheck.agrees:
            result.diagnostics.append(
                Diagnostic(
                    code="TOEPLITZ_CROSSCHECK_MISMATCH",
                    severity="warning",
                    stage="toeplitz-crosscheck",
                    message=crosscheck.note,
                )
            )

    return AnalysisOutcome(
        result=result, stages=list(PIPELINE_STAGES), toeplitz_crosscheck=crosscheck
    )


def _toeplitz_crosscheck(
    r: np.ndarray, order: int, coefficients: np.ndarray, settings: Settings
) -> ToeplitzCrosscheck:
    try:
        reference = solve_normal_equations_toeplitz(r, order)
    except np.linalg.LinAlgError:
        return ToeplitzCrosscheck(
            max_abs_deviation=None,
            agrees=False,
            note=(
                "Independent Toeplitz reference solver failed: the normal-equation "
                "matrix is singular (consistent with an order that is too high for "
                "this frame)."
            ),
        )
    deviation = float(np.max(np.abs(reference - coefficients)))
    agrees = deviation <= settings.toeplitz_tol
    note = (
        f"Levinson-Durbin matches the independent Toeplitz solve within {deviation:.3e}."
        if agrees
        else (
            f"Levinson-Durbin deviates from the independent Toeplitz solve by "
            f"{deviation:.3e} (tolerance {settings.toeplitz_tol:.1e})."
        )
    )
    return ToeplitzCrosscheck(
        max_abs_deviation=deviation, agrees=agrees, note=note
    )


def synthesize_frame(
    coefficients: np.ndarray,
    residual: np.ndarray,
    initial_state: np.ndarray | None,
) -> SynthesisOutcome:
    """Run the synthesis filter over one residual frame."""
    a = np.asarray(coefficients, dtype=float)
    order = a.size - 1
    filt = SynthesisFilter(order, initial_state)
    samples = filt.process(np.asarray(residual, dtype=float), a)
    return SynthesisOutcome(samples=samples, final_state=filt.state)


def reconstruction_metrics(
    original: np.ndarray,
    reconstructed: np.ndarray,
    residual_energy: float,
    frame_energy: float,
    settings: Settings,
) -> ReconstructionMetrics:
    """Compare reconstruction against the original — never against the residual."""
    x = np.asarray(original, dtype=float)
    x_hat = np.asarray(reconstructed, dtype=float)
    err = x - x_hat
    err_norm = float(np.linalg.norm(err))
    sig_norm = float(np.linalg.norm(x))
    if sig_norm == 0.0:
        relative = 0.0 if err_norm == 0.0 else float("inf")
    else:
        relative = err_norm / sig_norm
    ratio = (
        residual_energy / frame_energy if frame_energy > 0.0 else None
    )
    return ReconstructionMetrics(
        max_abs_error=float(np.max(np.abs(err))) if err.size else 0.0,
        rms_error=float(np.sqrt(np.mean(err**2))) if err.size else 0.0,
        relative_error=relative,
        residual_energy_ratio=ratio,
        verified=relative <= settings.reconstruction_tol,
        tolerance=settings.reconstruction_tol,
        note=VERIFICATION_NOTE,
    )


def roundtrip_frame(
    samples: np.ndarray,
    order: int,
    window: str,
    settings: Settings,
    *,
    verify_toeplitz: bool = False,
) -> tuple[AnalysisOutcome, SynthesisOutcome, ReconstructionMetrics]:
    """Analyse then synthesize with corresponding (zero) initial states."""
    analysis = analyze_frame(
        samples, order, window, settings, verify_toeplitz=verify_toeplitz
    )
    synthesis = synthesize_frame(
        analysis.result.coefficients, analysis.result.residual, initial_state=None
    )
    metrics = reconstruction_metrics(
        np.asarray(samples, dtype=float),
        synthesis.samples,
        analysis.result.residual_energy,
        analysis.result.frame_energy,
        settings,
    )
    return analysis, synthesis, metrics
