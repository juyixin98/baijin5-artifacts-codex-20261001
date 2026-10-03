"""Pipeline: orchestrates analysis, reconstruction and verification.

Each public function logs its key steps (frame layout, stability events,
verification outcome) under the current request id, and returns results
in the contract models. Failure reasons and uncertain conclusions are
returned in dedicated fields, never folded into "success" output.
"""

from __future__ import annotations

import numpy as np

from app.config import Settings
from app.contracts import (
    AnalyzeResponse,
    EncodedFrame,
    FrameResult,
    LPCConfigModel,
    ProcessingInfo,
    ReconstructResponse,
    RoundtripResponse,
    StabilityReport,
)
from app.lpc.autocorr import apply_window, autocorrelation
from app.lpc.levinson import DIAG_ZERO_ENERGY
from app.logging_utils import get_logger
from app.stream import LPCStreamAnalyzer, LPCStreamReconstructor
from app.verification import (
    VERDICT_LOSSLESS,
    assess_reconstruction,
    cross_check_toeplitz,
)

_MODULE = "lpc_backend.pipeline"


def _processing_info(
    config: LPCConfigModel, settings: Settings, n_samples: int, n_frames: int
) -> ProcessingInfo:
    return ProcessingInfo(
        version=settings.app_version,
        module=_MODULE,
        config=config,
        n_samples=n_samples,
        n_frames=n_frames,
    )


def _split_frames(samples: np.ndarray, frame_size: int) -> list[np.ndarray]:
    """Non-overlapping frames; the final frame is zero-padded."""
    n_frames = int(np.ceil(samples.size / frame_size))
    frames = []
    for i in range(n_frames):
        chunk = samples[i * frame_size : (i + 1) * frame_size]
        if chunk.size < frame_size:
            chunk = np.pad(chunk, (0, frame_size - chunk.size))
        frames.append(chunk)
    return frames


def analyze(
    samples: list[float], config: LPCConfigModel, request_id: str, settings: Settings
) -> AnalyzeResponse:
    logger = get_logger()
    x = np.asarray(samples, dtype=np.float64)
    frames = _split_frames(x, config.frame_size)
    logger.info(
        "analyze: n_samples=%d frame_size=%d order=%d window=%s -> %d frames",
        x.size,
        config.frame_size,
        config.order,
        config.window,
        len(frames),
    )

    analyzer = LPCStreamAnalyzer(config.frame_size, config.order, config.window)
    diagnostics: list[str] = []
    uncertain: list[str] = []
    out_frames: list[FrameResult] = []

    for frame in frames:
        fa = analyzer.process_frame(frame)
        lev = fa.levinson
        for diag in lev.diagnostics:
            msg = f"frame {fa.frame_index}: {diag}"
            if diag == DIAG_ZERO_ENERGY:
                logger.info(msg)
            else:
                logger.warning(msg)
            diagnostics.append(msg)

        # Independent cross-check against the Toeplitz reference solve.
        windowed = apply_window(frame, config.window)
        r = autocorrelation(windowed, config.order)
        agrees, dev, ref_diag = cross_check_toeplitz(r, config.order, lev.lpc)
        if ref_diag is not None:
            msg = f"frame {fa.frame_index}: reference solver: {ref_diag}"
            logger.warning(msg)
            uncertain.append(msg)
        elif not agrees:
            msg = (
                f"frame {fa.frame_index}: levinson/toeplitz deviation "
                f"{dev:.3e} exceeds tolerance"
            )
            logger.warning(msg)
            uncertain.append(msg)

        out_frames.append(
            FrameResult(
                frame_index=fa.frame_index,
                offset=fa.offset,
                lpc=lev.lpc.tolist(),
                reflection_coeffs=lev.reflection_coeffs.tolist(),
                gain=lev.gain,
                residual=fa.residual.tolist(),
                zero_energy=lev.zero_energy,
                stability=StabilityReport(
                    stable=lev.stable,
                    max_abs_reflection=lev.max_abs_reflection,
                    diagnostics=list(lev.diagnostics),
                ),
            )
        )

    logger.info(
        "analyze done: frames=%d diagnostics=%d uncertain=%d",
        len(out_frames),
        len(diagnostics),
        len(uncertain),
    )
    return AnalyzeResponse(
        request_id=request_id,
        processing=_processing_info(config, settings, x.size, len(out_frames)),
        frames=out_frames,
        diagnostics=diagnostics,
        uncertain=uncertain,
    )


def reconstruct(
    frames: list[EncodedFrame],
    config: LPCConfigModel,
    request_id: str,
    settings: Settings,
) -> ReconstructResponse:
    logger = get_logger()
    reconstructor = LPCStreamReconstructor(config.order)
    chunks: list[np.ndarray] = []
    diagnostics: list[str] = []
    for i, enc in enumerate(frames):
        lpc = np.asarray(enc.lpc, dtype=np.float64)
        if lpc.size - 1 != config.order:
            msg = (
                f"frame {i}: lpc length {lpc.size} implies order {lpc.size - 1}, "
                f"config order is {config.order}"
            )
            logger.error(msg)
            raise ValueError(msg)
        if enc.zero_energy:
            diagnostics.append(f"frame {i}: {DIAG_ZERO_ENERGY}")
        chunks.append(
            reconstructor.reconstruct_frame(np.asarray(enc.residual), lpc)
        )
    samples = np.concatenate(chunks) if chunks else np.zeros(0)
    logger.info("reconstruct done: frames=%d samples=%d", len(frames), samples.size)
    return ReconstructResponse(
        request_id=request_id,
        processing=_processing_info(config, settings, samples.size, len(frames)),
        samples=samples.tolist(),
        diagnostics=diagnostics,
    )


def roundtrip(
    samples: list[float], config: LPCConfigModel, request_id: str, settings: Settings
) -> RoundtripResponse:
    """Analyze then reconstruct, and judge by direct reconstruction error."""
    logger = get_logger()
    analysis = analyze(samples, config, request_id, settings)
    encoded = [
        EncodedFrame(lpc=f.lpc, residual=f.residual, zero_energy=f.zero_energy)
        for f in analysis.frames
    ]
    recon = reconstruct(encoded, config, request_id, settings)

    x = np.asarray(samples, dtype=np.float64)
    y = np.asarray(recon.samples[: x.size], dtype=np.float64)
    residual_all = np.concatenate(
        [np.asarray(f.residual) for f in analysis.frames]
    )[: x.size]
    assessment = assess_reconstruction(
        x, y, residual_all, rel_tol=settings.lossless_rel_tol
    )

    uncertain = list(analysis.uncertain)
    if assessment.verdict != VERDICT_LOSSLESS:
        uncertain.extend(assessment.reasons)
        logger.warning("roundtrip verdict=%s: %s", assessment.verdict, assessment.reasons)
    else:
        logger.info("roundtrip verdict=%s rel_err=%.3e", assessment.verdict, assessment.relative_error)

    metrics = {
        "max_abs_error": assessment.max_abs_error,
        "relative_error": assessment.relative_error,
        "residual_energy_ratio": assessment.residual_energy_ratio,
        "lossless_rel_tol": settings.lossless_rel_tol,
    }
    return RoundtripResponse(
        request_id=request_id,
        processing=analysis.processing,
        metrics=metrics,
        lossless_confirmed=assessment.lossless_confirmed,
        verdict=assessment.verdict,
        reasons=list(assessment.reasons),
        diagnostics=analysis.diagnostics,
        uncertain=uncertain,
    )
