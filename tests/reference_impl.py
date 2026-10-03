"""Independent reference implementation used to cross-check mfcc_backend.

Deliberately written with explicit Python loops and direct formulas, and
sharing NO code with the package under test, so a bug in mfcc_backend
cannot silently reproduce itself here. Reference answers in the test-suite
come from this module or from hand-computed constants — never from the
implementation under test.
"""

from __future__ import annotations

import math

import numpy as np


def preemphasis(x, coef: float) -> np.ndarray:
    x = [float(v) for v in x]
    out = [x[0]]  # boundary rule: x[-1] = 0
    for n in range(1, len(x)):
        out.append(x[n] - coef * x[n - 1])
    return np.array(out)


def hamming(n: int) -> np.ndarray:
    return np.array(
        [0.54 - 0.46 * math.cos(2.0 * math.pi * i / (n - 1)) for i in range(n)]
    )


def frames(x, frame_length: int, hop: int) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if len(x) < frame_length:
        raise ValueError("signal shorter than one frame")
    w = hamming(frame_length)
    n_frames = 1 + (len(x) - frame_length) // hop
    return np.array([x[i * hop : i * hop + frame_length] * w for i in range(n_frames)])


def power_spectrum(frame_matrix: np.ndarray, nfft: int) -> np.ndarray:
    out = []
    for frame in frame_matrix:
        spec = np.fft.rfft(frame, n=nfft)
        out.append((spec.real**2 + spec.imag**2) / nfft)
    return np.array(out)


def hz_to_mel(f: float) -> float:
    return 2595.0 * math.log10(1.0 + f / 700.0)


def mel_to_hz(m: float) -> float:
    return 700.0 * (10.0 ** (m / 2595.0) - 1.0)


def mel_filterbank(
    sample_rate: int, nfft: int, n_mels: int, fmin: float, fmax: float
) -> np.ndarray:
    n_bins = nfft // 2 + 1
    mel_lo, mel_hi = hz_to_mel(fmin), hz_to_mel(fmax)
    hz_points = [
        mel_to_hz(mel_lo + i * (mel_hi - mel_lo) / (n_mels + 1))
        for i in range(n_mels + 2)
    ]
    freqs = [k * sample_rate / nfft for k in range(n_bins)]
    bank = np.zeros((n_mels, n_bins))
    for m in range(n_mels):
        left, center, right = hz_points[m], hz_points[m + 1], hz_points[m + 2]
        for k, f in enumerate(freqs):
            up = (f - left) / (center - left)
            down = (right - f) / (right - center)
            bank[m, k] = max(0.0, min(up, down))
    return bank


def dct_ii_ortho(x) -> np.ndarray:
    """Orthonormal DCT-II, computed straight from the definition."""
    x = [float(v) for v in x]
    m = len(x)
    out = []
    for k in range(m):
        s = sum(x[n] * math.cos(math.pi * k * (2 * n + 1) / (2.0 * m)) for n in range(m))
        scale = math.sqrt(1.0 / m) if k == 0 else math.sqrt(2.0 / m)
        out.append(scale * s)
    return np.array(out)


def delta(feats: np.ndarray, width: int) -> np.ndarray:
    feats = np.asarray(feats, dtype=np.float64)
    n_frames = feats.shape[0]
    denom = 2.0 * sum(n * n for n in range(1, width + 1))

    def at(t: int) -> np.ndarray:
        return feats[min(max(t, 0), n_frames - 1)]  # edge replication

    out = np.zeros_like(feats)
    for t in range(n_frames):
        acc = np.zeros(feats.shape[1])
        for n in range(1, width + 1):
            acc = acc + n * (at(t + n) - at(t - n))
        out[t] = acc / denom
    return out


def mfcc_pipeline(
    samples,
    sample_rate: int = 16000,
    frame_length: int = 400,
    hop: int = 160,
    preemph: float = 0.97,
    n_mels: int = 26,
    n_mfcc: int = 13,
    fmin: float = 20.0,
    fmax: float = 8000.0,
    log_floor: float = 1e-10,
    delta_width: int = 2,
) -> dict:
    """Full reference chain returning every intermediate matrix."""
    pe = preemphasis(samples, preemph)
    fr = frames(pe, frame_length, hop)
    power = power_spectrum(fr, frame_length)  # nfft == frame_length
    bank = mel_filterbank(sample_rate, frame_length, n_mels, fmin, fmax)
    mel_e = power @ bank.T
    log_mel = np.log(np.maximum(mel_e, log_floor))
    mfcc = np.array([dct_ii_ortho(row)[:n_mfcc] for row in log_mel])
    d1 = delta(mfcc, delta_width)
    d2 = delta(d1, delta_width)
    return {
        "preemphasized": pe,
        "frames": fr,
        "power": power,
        "mel_energies": mel_e,
        "log_mel": log_mel,
        "mfcc": mfcc,
        "delta": d1,
        "delta2": d2,
    }
