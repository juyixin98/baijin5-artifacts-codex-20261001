"""Batch pipeline: samples -> every intermediate matrix -> MFCC/delta/delta2.

Returning all intermediates (not just the final features) is deliberate:
the numeric test-suite cross-checks each stage against an independent
reference implementation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import MFCCConfig
from .dsp import (
    build_mel_filterbank,
    compute_delta,
    frame_signal,
    log_mel_spectrum,
    mfcc_from_log_mel,
    power_spectrum,
    preemphasis,
)
from .errors import InvalidAudioError


def validate_samples(samples, *, allow_empty: bool = False) -> np.ndarray:
    """Boundary validation for raw audio: 1-D, non-empty, finite float64."""
    try:
        arr = np.asarray(samples, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise InvalidAudioError(f"samples are not a numeric sequence: {exc}") from exc
    if arr.ndim != 1:
        raise InvalidAudioError(
            f"samples must be a 1-D sequence, got shape {arr.shape}",
            detail={"shape": list(arr.shape)},
        )
    if arr.size == 0 and not allow_empty:
        raise InvalidAudioError("samples must contain at least one value")
    if arr.size and not np.all(np.isfinite(arr)):
        bad = int(np.count_nonzero(~np.isfinite(arr)))
        raise InvalidAudioError(
            f"samples contain {bad} NaN/Inf value(s)",
            detail={"non_finite": bad},
        )
    return arr


@dataclass
class PipelineResult:
    config: MFCCConfig
    n_input_samples: int
    preemphasized: np.ndarray
    frames: np.ndarray
    power: np.ndarray
    mel_energies: np.ndarray
    log_mel: np.ndarray
    mfcc: np.ndarray
    delta: np.ndarray
    delta2: np.ndarray

    @property
    def n_frames(self) -> int:
        return int(self.frames.shape[0])


def compute_pipeline(samples, config: MFCCConfig | None = None) -> PipelineResult:
    config = (config or MFCCConfig()).validate()
    x = validate_samples(samples)

    pe = preemphasis(x, config.preemphasis_coef)
    frames = frame_signal(pe, config.frame_length, config.hop_length)
    power = power_spectrum(frames, config.nfft)
    bank = build_mel_filterbank(config)
    mel_energies = power @ bank.T
    log_mel = log_mel_spectrum(mel_energies, config.log_floor)
    mfcc = mfcc_from_log_mel(log_mel, config.n_mfcc)
    delta = compute_delta(mfcc, config.delta_width)
    delta2 = compute_delta(delta, config.delta_width)

    return PipelineResult(
        config=config,
        n_input_samples=int(x.size),
        preemphasized=pe,
        frames=frames,
        power=power,
        mel_energies=mel_energies,
        log_mel=log_mel,
        mfcc=mfcc,
        delta=delta,
        delta2=delta2,
    )
