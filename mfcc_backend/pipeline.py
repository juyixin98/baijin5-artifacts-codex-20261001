"""Batch MFCC pipeline: samples -> frames -> power spectrum -> log-mel -> MFCC.

The intermediate matrices (frames, power spectrum, log-mel) are returned
alongside the final features so tests and callers can verify each stage
against an independent implementation instead of only the end result.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.fft import dct as _scipy_dct

from .config import DEFAULT_CONFIG, MFCCConfig
from .delta import compute_delta, compute_delta_delta
from .filterbank import build_mel_filterbank
from .framing import (
    frame_signal,
    hamming_window,
    preemphasis,
    validate_samples,
)


@dataclass(frozen=True)
class PipelineResult:
    """All stages of one batch extraction.  Shapes for T frames:

    frames:      (T, frame_length)   windowed, pre-emphasized frames
    power:       (T, n_freq_bins)    |rfft|^2 / n_fft
    log_mel:     (T, n_mels)         ln(max(mel energy, log_floor))
    mfcc:        (T, n_mfcc)
    delta:       (T, n_mfcc)
    delta_delta: (T, n_mfcc)
    """

    mfcc: np.ndarray
    delta: np.ndarray
    delta_delta: np.ndarray
    log_mel: np.ndarray
    power: np.ndarray
    frames: np.ndarray
    n_frames: int
    n_samples: int
    config: MFCCConfig


def dct_ii_ortho(log_mel: np.ndarray, n_mfcc: int) -> np.ndarray:
    """DCT-II with orthonormal scaling, keeping the first n_mfcc coefficients."""
    return _scipy_dct(log_mel, type=2, axis=1, norm="ortho")[:, :n_mfcc]


def apply_lifter(mfcc: np.ndarray, lifter: int) -> np.ndarray:
    """Standard sine lifter; a no-op when lifter == 0."""
    if lifter <= 0 or mfcc.shape[0] == 0:
        return mfcc
    n = np.arange(mfcc.shape[1])
    lift = 1.0 + (lifter / 2.0) * np.sin(np.pi * n / lifter)
    return mfcc * lift


def log_mel_spectrogram(
    samples: np.ndarray,
    config: MFCCConfig = DEFAULT_CONFIG,
    *,
    filterbank: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute (frames, power, log_mel) for pre-validated samples."""
    config.validate()
    if filterbank is None:
        filterbank = build_mel_filterbank(config)
    emphasized = preemphasis(samples, config.preemphasis)
    frames = frame_signal(emphasized, config.frame_length, config.hop_length)
    windowed = frames * hamming_window(config.frame_length)
    if frames.shape[0] == 0:
        power = np.empty((0, config.n_freq_bins))
        log_mel = np.empty((0, config.n_mels))
        return windowed, power, log_mel
    spectrum = np.abs(np.fft.rfft(windowed, n=config.n_fft, axis=1)) ** 2
    power = spectrum / config.n_fft
    mel_energy = power @ filterbank.T
    log_mel = np.log(np.maximum(mel_energy, config.log_floor))
    return windowed, power, log_mel


def extract_features(
    samples: np.ndarray,
    config: MFCCConfig = DEFAULT_CONFIG,
    *,
    max_samples: int | None = None,
) -> PipelineResult:
    """Full batch pipeline.  Raises InputContractError / ConfigError /
    EmptyFilterError on their respective contract violations."""
    x = validate_samples(samples, max_samples=max_samples)
    frames, power, log_mel = log_mel_spectrogram(x, config)
    mfcc = apply_lifter(dct_ii_ortho(log_mel, config.n_mfcc), config.lifter)
    return PipelineResult(
        mfcc=mfcc,
        delta=compute_delta(mfcc, config.delta_width),
        delta_delta=compute_delta_delta(mfcc, config.delta_width),
        log_mel=log_mel,
        power=power,
        frames=frames,
        n_frames=mfcc.shape[0],
        n_samples=int(x.size),
        config=config,
    )
