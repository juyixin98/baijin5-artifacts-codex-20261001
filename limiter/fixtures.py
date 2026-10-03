"""Deterministic synthetic PCM fixtures.

All fixtures are built from closed-form formulas (no RNG), so any test
failure can be reproduced bit-exactly from the fixture id alone. Each
fixture carries a sha256 fingerprint used in test logs to correlate a
failure with the exact input block.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

import numpy as np

DEFAULT_SR = 48000


@dataclass(frozen=True)
class Fixture:
    id: str
    description: str
    build: Callable[[], np.ndarray]
    meta: dict

    def pcm(self) -> np.ndarray:
        return self.build()

    def sha256(self) -> str:
        return fixture_hash(self.pcm())


def fixture_hash(pcm: np.ndarray) -> str:
    arr = np.ascontiguousarray(np.asarray(pcm, dtype=np.float64))
    return hashlib.sha256(arr.tobytes()).hexdigest()


def _t(n: int, sr: int) -> np.ndarray:
    return np.arange(n, dtype=np.float64) / sr


def impulse(n: int = 4096, index: int = 1000, amplitude: float = 2.0, channels: int = 2) -> Fixture:
    """Single-sample impulse in digital silence (latency/attack probe)."""

    def build() -> np.ndarray:
        x = np.zeros((n, channels), dtype=np.float64)
        x[index, :] = amplitude
        return x

    return Fixture(
        id="impulse",
        description=f"1-sample impulse amp={amplitude} at frame {index} of {n}",
        build=build,
        meta={"n": n, "index": index, "amplitude": amplitude, "channels": channels},
    )


def stereo_imbalanced(
    n: int = 9600,
    freq: float = 1000.0,
    left_amp: float = 2.0,
    right_amp: float = 0.2,
    sr: int = DEFAULT_SR,
) -> Fixture:
    """Loud left / quiet right sine pair (channel-linking probe)."""

    def build() -> np.ndarray:
        t = _t(n, sr)
        left = left_amp * np.sin(2.0 * np.pi * freq * t)
        right = right_amp * np.sin(2.0 * np.pi * freq * t)
        return np.column_stack([left, right])

    return Fixture(
        id="stereo_imbalanced",
        description=f"sine {freq}Hz L={left_amp} R={right_amp}, {n} frames",
        build=build,
        meta={"n": n, "freq": freq, "left_amp": left_amp, "right_amp": right_amp, "sr": sr},
    )


def sustained_sine(
    n: int = 14400,
    freq: float = 1000.0,
    amp: float = 0.9,
    sr: int = DEFAULT_SR,
    channels: int = 2,
) -> Fixture:
    """Continuous over-threshold sine (steady-state gain / release probe)."""

    def build() -> np.ndarray:
        t = _t(n, sr)
        x = amp * np.sin(2.0 * np.pi * freq * t)
        return np.column_stack([x] * channels)

    return Fixture(
        id="sustained_sine",
        description=f"sustained sine {freq}Hz amp={amp}, {n} frames",
        build=build,
        meta={"n": n, "freq": freq, "amp": amp, "sr": sr, "channels": channels},
    )


def boundary_burst(
    n: int = 5000,
    block_size: int = 1024,
    amp: float = 3.0,
    bg_amp: float = 0.3,
    bg_freq: float = 997.0,
    sr: int = DEFAULT_SR,
    channels: int = 2,
) -> Fixture:
    """Impulse burst straddling a block boundary on a quiet sine bed."""

    positions = (block_size - 2, block_size - 1, block_size)

    def build() -> np.ndarray:
        t = _t(n, sr)
        bed = bg_amp * np.sin(2.0 * np.pi * bg_freq * t)
        x = np.column_stack([bed] * channels)
        for p in positions:
            x[p, :] = amp
        return x

    return Fixture(
        id="boundary_burst",
        description=f"burst amp={amp} at frames {positions} (block={block_size}) over {bg_amp} sine",
        build=build,
        meta={
            "n": n,
            "block_size": block_size,
            "positions": positions,
            "amp": amp,
            "bg_amp": bg_amp,
            "sr": sr,
            "channels": channels,
        },
    )


def intersample_crest(
    n: int = 4800,
    freq: float = 10000.0,
    amp: float = 0.502,  # just above the default threshold 0.5
    sr: int = DEFAULT_SR,
    channels: int = 2,
) -> Fixture:
    """Sine whose TRUE peak exceeds the threshold while every SAMPLE peak
    stays below it. At 10 kHz / 48 kHz the sampled phases land on a 15
    degree grid; the half-sample phase offset puts the nearest sample
    7.5 degrees from each crest, so sample peak = amp*cos(7.5deg) < 0.5
    while the reconstructed crest reaches amp > 0.5. Probes the
    sample-peak vs true-peak contract boundary."""

    def build() -> np.ndarray:
        t = _t(n, sr)
        phase = 2.0 * np.pi * freq * t + np.pi * freq / sr  # half-sample offset
        x = amp * np.sin(phase)
        return np.column_stack([x] * channels)

    return Fixture(
        id="intersample_crest",
        description=f"sine {freq}Hz amp={amp} with half-sample phase offset, {n} frames",
        build=build,
        meta={"n": n, "freq": freq, "amp": amp, "sr": sr, "channels": channels},
    )


FIXTURE_BUILDERS = {
    "impulse": impulse,
    "stereo_imbalanced": stereo_imbalanced,
    "sustained_sine": sustained_sine,
    "boundary_burst": boundary_burst,
    "intersample_crest": intersample_crest,
}
