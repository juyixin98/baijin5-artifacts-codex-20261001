"""Independent reference implementation of the MFCC pipeline.

This module deliberately shares **no code** with ``mfcc_backend``: every
stage is written out as literal loops straight from the formulas, so a bug in
the vectorized production code cannot silently reproduce itself here.  It is
slow by design and only used by the test-suite.

Reference answers in the tests therefore come from this file (plus analytic
closed forms), never from the implementation under test.
"""

from __future__ import annotations

import math


def ref_preemphasis(samples: list[float], coef: float) -> list[float]:
    out = []
    prev = 0.0
    for x in samples:
        out.append(x - coef * prev)
        prev = x
    return out


def ref_hamming(n: int) -> list[float]:
    # Symmetric Hamming: w[k] = 0.54 - 0.46 cos(2 pi k / (n-1))
    if n == 1:
        return [1.0]
    return [0.54 - 0.46 * math.cos(2.0 * math.pi * k / (n - 1)) for k in range(n)]


def ref_frame_count(n_samples: int, frame_length: int, hop: int) -> int:
    if n_samples < frame_length:
        return 0
    return 1 + (n_samples - frame_length) // hop


def ref_frames(samples: list[float], frame_length: int, hop: int) -> list[list[float]]:
    n = ref_frame_count(len(samples), frame_length, hop)
    return [samples[t * hop : t * hop + frame_length] for t in range(n)]


def ref_hz_to_mel(f: float) -> float:
    return 2595.0 * math.log10(1.0 + f / 700.0)


def ref_mel_to_hz(m: float) -> float:
    return 700.0 * (10.0 ** (m / 2595.0) - 1.0)


def ref_filterbank(
    n_mels: int, n_fft: int, sample_rate: int, fmin: float, fmax: float
) -> list[list[float]]:
    """Triangular filters evaluated per FFT bin with literal loops."""
    lo, hi = ref_hz_to_mel(fmin), ref_hz_to_mel(fmax)
    pts = [ref_mel_to_hz(lo + (hi - lo) * i / (n_mels + 1)) for i in range(n_mels + 2)]
    n_bins = n_fft // 2 + 1
    fb = []
    for m in range(n_mels):
        left, center, right = pts[m], pts[m + 1], pts[m + 2]
        row = []
        for k in range(n_bins):
            f = k * sample_rate / n_fft
            if f < left or f > right:
                row.append(0.0)
            elif f <= center:
                row.append((f - left) / (center - left))
            else:
                row.append((right - f) / (right - center))
        fb.append(row)
    return fb


def ref_dft_power(frame: list[float], n_fft: int) -> list[float]:
    """Power spectrum |rfft|^2 / n_fft via the literal DFT sum."""
    n_bins = n_fft // 2 + 1
    padded = list(frame) + [0.0] * (n_fft - len(frame))
    power = []
    for k in range(n_bins):
        re = sum(padded[n] * math.cos(-2.0 * math.pi * k * n / n_fft) for n in range(n_fft))
        im = sum(padded[n] * math.sin(-2.0 * math.pi * k * n / n_fft) for n in range(n_fft))
        power.append((re * re + im * im) / n_fft)
    return power


def ref_log_mel(
    samples: list[float],
    *,
    sample_rate: int,
    preemphasis: float,
    frame_length: int,
    hop: int,
    n_fft: int,
    n_mels: int,
    fmin: float,
    fmax: float,
    log_floor: float,
) -> list[list[float]]:
    emphasized = ref_preemphasis(samples, preemphasis)
    window = ref_hamming(frame_length)
    fb = ref_filterbank(n_mels, n_fft, sample_rate, fmin, fmax)
    out = []
    for frame in ref_frames(emphasized, frame_length, hop):
        windowed = [x * w for x, w in zip(frame, window)]
        power = ref_dft_power(windowed, n_fft)
        row = []
        for m in range(n_mels):
            energy = sum(fb[m][k] * power[k] for k in range(n_fft // 2 + 1))
            row.append(math.log(max(energy, log_floor)))
        out.append(row)
    return out


def ref_dct_ii_ortho(row: list[float], n_mfcc: int) -> list[float]:
    """DCT-II with ortho norm: c[j] = sqrt(2/N)*s(j) * sum x[i] cos(pi j (2i+1) / 2N),
    s(0) = 1/sqrt(2), s(j>0) = 1."""
    n = len(row)
    out = []
    for j in range(n_mfcc):
        acc = sum(
            row[i] * math.cos(math.pi * j * (2 * i + 1) / (2.0 * n)) for i in range(n)
        )
        scale = math.sqrt(2.0 / n) * (1.0 / math.sqrt(2.0) if j == 0 else 1.0)
        out.append(scale * acc)
    return out


def ref_mfcc(log_mel: list[list[float]], n_mfcc: int) -> list[list[float]]:
    return [ref_dct_ii_ortho(row, n_mfcc) for row in log_mel]


def ref_delta(features: list[list[float]], width: int) -> list[list[float]]:
    """Delta with edge replication, literal loops."""
    t_max = len(features)
    if t_max == 0:
        return []
    n_coeffs = len(features[0])
    denom = 2.0 * sum(n * n for n in range(1, width + 1))
    out = []
    for t in range(t_max):
        row = []
        for c in range(n_coeffs):
            acc = 0.0
            for n in range(1, width + 1):
                up = features[min(t + n, t_max - 1)][c]
                down = features[max(t - n, 0)][c]
                acc += n * (up - down)
            row.append(acc / denom)
        out.append(row)
    return out
