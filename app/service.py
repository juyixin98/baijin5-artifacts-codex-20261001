"""Business logic between the HTTP layer and the LPC pipeline.

Each function logs its key steps with the ambient request id (see
``app.logging_config``) and returns contract models, so routes stay thin
and every response is explainable: what ran, which version, what failed,
what is uncertain.
"""
from __future__ import annotations

import numpy as np

from app.config import Settings
from app.contracts import (
    AnalysisPayload,
    DiagnosticModel,
    ReconstructionMetricsModel,
    ToeplitzCrosscheckModel,
    validate_against_settings,
)
from app.lpc.pipeline import (
    AnalysisOutcome,
    ReconstructionMetrics,
    ToeplitzCrosscheck,
    analyze_frame,
    reconstruction_metrics,
    roundtrip_frame,
    synthesize_frame,
)
from app.logging_config import get_logger
from app.stream import StreamManager, StreamSession

logger = get_logger("lpc.service")


class DomainError(Exception):
    """Error with a stable code, safe to surface to the API caller."""

    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def _resolve(params, settings: Settings) -> tuple[int, str]:
    order = params.order if params.order is not None else settings.default_order
    window = params.window if params.window is not None else settings.default_window
    return order, window


def _payload(outcome: AnalysisOutcome, frame_length: int) -> AnalysisPayload:
    r = outcome.result
    return AnalysisPayload(
        order=r.order,
        window=r.window,
        frame_length=frame_length,
        frame_energy=r.frame_energy,
        coefficients=r.coefficients.tolist(),
        reflection_coefficients=list(r.reflection_coefficients),
        prediction_error_energy=r.prediction_error_energy,
        gain=r.gain,
        residual=r.residual.tolist(),
        residual_energy=r.residual_energy,
        analysis_final_state=r.analysis_final_state.tolist(),
        max_pole_magnitude=r.max_pole_magnitude,
        stable=r.stable,
        completed_order=r.completed_order,
        diagnostics=[
            DiagnosticModel(code=d.code, severity=d.severity, stage=d.stage, message=d.message)
            for d in r.diagnostics
        ],
        errors=r.errors,
        warnings=r.warnings,
    )


def _crosscheck_model(cc: ToeplitzCrosscheck | None) -> ToeplitzCrosscheckModel | None:
    if cc is None:
        return None
    return ToeplitzCrosscheckModel(
        max_abs_deviation=cc.max_abs_deviation, agrees=cc.agrees, note=cc.note
    )


def _metrics_model(m: ReconstructionMetrics) -> ReconstructionMetricsModel:
    return ReconstructionMetricsModel(
        max_abs_error=m.max_abs_error,
        rms_error=m.rms_error,
        relative_error=m.relative_error,
        residual_energy_ratio=m.residual_energy_ratio,
        verified=m.verified,
        tolerance=m.tolerance,
        note=m.note,
    )


def _check_bounds(params, settings: Settings) -> None:
    try:
        validate_against_settings(params, settings)
    except ValueError as exc:
        raise DomainError("INVALID_FRAME_PARAMETERS", str(exc), http_status=422) from exc


def analyze(params, settings: Settings) -> tuple[AnalysisPayload, ToeplitzCrosscheckModel | None, list[str]]:
    _check_bounds(params, settings)
    order, window = _resolve(params, settings)
    logger.info(
        "analyze start frame_length=%d order=%d window=%s verify_toeplitz=%s",
        len(params.samples), order, window, params.verify_toeplitz,
    )
    outcome = analyze_frame(
        np.asarray(params.samples, dtype=float),
        order,
        window,
        settings,
        verify_toeplitz=params.verify_toeplitz,
    )
    r = outcome.result
    logger.info(
        "analyze done stable=%s completed_order=%d gain=%.6e errors=%d warnings=%d",
        r.stable, r.completed_order, r.gain, len(r.errors), len(r.warnings),
    )
    return _payload(outcome, len(params.samples)), _crosscheck_model(outcome.toeplitz_crosscheck), outcome.stages


def synthesize(params, settings: Settings) -> tuple[list[float], list[float], list[str]]:
    order = len(params.coefficients) - 1
    if order > settings.max_order:
        raise DomainError(
            "INVALID_FRAME_PARAMETERS",
            f"order {order} exceeds the maximum of {settings.max_order}",
            http_status=422,
        )
    if len(params.residual) > settings.max_frame_size:
        raise DomainError(
            "INVALID_FRAME_PARAMETERS",
            f"residual of {len(params.residual)} samples exceeds the maximum "
            f"of {settings.max_frame_size}",
            http_status=422,
        )
    logger.info(
        "synthesize start order=%d residual_length=%d state=%s",
        order,
        len(params.residual),
        "provided" if params.initial_state is not None else "zero",
    )
    outcome = synthesize_frame(
        np.asarray(params.coefficients, dtype=float),
        np.asarray(params.residual, dtype=float),
        None if params.initial_state is None else np.asarray(params.initial_state, dtype=float),
    )
    logger.info("synthesize done output_length=%d", outcome.samples.size)
    return outcome.samples.tolist(), outcome.final_state.tolist(), ["synthesis-filter"]


def roundtrip(params, settings: Settings):
    _check_bounds(params, settings)
    order, window = _resolve(params, settings)
    logger.info(
        "roundtrip start frame_length=%d order=%d window=%s",
        len(params.samples), order, window,
    )
    analysis, synthesis, metrics = roundtrip_frame(
        np.asarray(params.samples, dtype=float),
        order,
        window,
        settings,
        verify_toeplitz=params.verify_toeplitz,
    )
    logger.info(
        "roundtrip done verified=%s relative_error=%.3e residual_energy_ratio=%s",
        metrics.verified,
        metrics.relative_error,
        f"{metrics.residual_energy_ratio:.3e}" if metrics.residual_energy_ratio is not None else "n/a",
    )
    return (
        _payload(analysis, len(params.samples)),
        _crosscheck_model(analysis.toeplitz_crosscheck),
        synthesis.samples.tolist(),
        _metrics_model(metrics),
        analysis.stages,
    )


# ------------------------------------------------------------------ streams


def stream_create(params, manager: StreamManager, settings: Settings) -> StreamSession:
    order = params.order if params.order is not None else settings.default_order
    window = params.window if params.window is not None else settings.default_window
    if order > settings.max_order:
        raise DomainError(
            "INVALID_FRAME_PARAMETERS",
            f"order {order} exceeds the maximum of {settings.max_order}",
            http_status=422,
        )
    session = manager.create(order, window)
    logger.info("stream created stream_id=%s order=%d window=%s", session.stream_id, order, window)
    return session


def stream_get(stream_id: str, manager: StreamManager) -> StreamSession:
    try:
        return manager.get(stream_id)
    except KeyError:
        raise DomainError("STREAM_NOT_FOUND", f"unknown stream id: {stream_id}", http_status=404) from None


def stream_delete(stream_id: str, manager: StreamManager) -> None:
    try:
        manager.delete(stream_id)
    except KeyError:
        raise DomainError("STREAM_NOT_FOUND", f"unknown stream id: {stream_id}", http_status=404) from None
    logger.info("stream deleted stream_id=%s", stream_id)


def stream_push_frame(session: StreamSession, samples: list[float], mode: str, settings: Settings):
    x = np.asarray(samples, dtype=float)
    if x.size > settings.max_frame_size:
        raise DomainError(
            "INVALID_FRAME_PARAMETERS",
            f"frame of {x.size} samples exceeds the maximum of {settings.max_frame_size}",
            http_status=422,
        )
    if session.order >= x.size:
        raise DomainError(
            "INVALID_FRAME_PARAMETERS",
            f"order {session.order} must be smaller than the frame length {x.size}",
            http_status=422,
        )
    logger.info(
        "stream frame stream_id=%s frame_index=%d mode=%s frame_length=%d",
        session.stream_id, session.frames_processed, mode, x.size,
    )
    outcome = analyze_frame(
        x,
        session.order,
        session.window,
        settings,
        analysis_filter=session.analysis_filter,
    )
    reconstructed = None
    metrics = None
    if mode == "roundtrip":
        rec = session.synthesis_filter.process(
            outcome.result.residual, outcome.result.coefficients
        )
        reconstructed = rec.tolist()
        metrics = _metrics_model(
            reconstruction_metrics(
                x,
                rec,
                outcome.result.residual_energy,
                outcome.result.frame_energy,
                settings,
            )
        )
    session.frames_processed += 1
    logger.info(
        "stream frame done stream_id=%s frame_index=%d stable=%s",
        session.stream_id, session.frames_processed - 1, outcome.result.stable,
    )
    return (
        _payload(outcome, x.size),
        reconstructed,
        metrics,
        session.frames_processed,
        outcome.stages,
    )
