"""Synthetic fixtures: deterministic plants, noise and reference signals.

Everything is seeded and local — no external data. The clean signal is
always available alongside the corrupted one so evaluation can be done
against ground truth (see app.algorithms.metrics).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.signal import lfilter


def white_noise(n: int, seed: int, scale: float = 1.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return scale * rng.standard_normal(n)


def clean_tone(n: int, freq: float = 0.05, amplitude: float = 0.5) -> np.ndarray:
    t = np.arange(n, dtype=np.float64)
    return amplitude * np.sin(2.0 * np.pi * freq * t)


def make_plant(length: int, seed: int, decay: float = 0.08) -> np.ndarray:
    """Deterministic exponentially-damped random impulse response."""
    rng = np.random.default_rng(seed)
    taps = rng.standard_normal(length) * np.exp(-decay * np.arange(length))
    return taps / (np.linalg.norm(taps) + 1e-12)


def apply_plant(plant: np.ndarray, x: np.ndarray) -> np.ndarray:
    return lfilter(plant, [1.0], x)


@dataclass(frozen=True)
class Scenario:
    """A fully-known test scene: reference x, desired d = clean + noise."""

    name: str
    reference: np.ndarray
    desired: np.ndarray
    clean: np.ndarray
    plants: list[np.ndarray] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


def correlated_noise_scenario(
    n: int = 4000, filter_length: int = 64, seed: int = 7, noise_scale: float = 1.0
) -> Scenario:
    """Noise is the reference passed through a known plant -> identifiable."""
    plant = make_plant(filter_length, seed)
    reference = white_noise(n, seed + 1)
    noise = noise_scale * apply_plant(plant, reference)
    clean = clean_tone(n)
    return Scenario(
        name="correlated_noise",
        reference=reference,
        desired=clean + noise,
        clean=clean,
        plants=[plant],
        meta={"seed": seed, "noise_scale": noise_scale},
    )


def decorrelated_reference_scenario(
    n: int = 4000, filter_length: int = 64, seed: int = 21
) -> Scenario:
    """Noise is independent of the reference -> no linear filter of x can
    cancel it. This is the documented failure case: noise reduction must
    stay near zero and the clean signal must not be claimed recovered."""
    plant = make_plant(filter_length, seed)
    reference = white_noise(n, seed + 1)
    noise = apply_plant(plant, white_noise(n, seed + 2))  # independent of reference
    clean = clean_tone(n)
    return Scenario(
        name="decorrelated_reference",
        reference=reference,
        desired=clean + noise,
        clean=clean,
        plants=[plant],
        meta={"seed": seed, "expected": "failure: no usable reference correlation"},
    )


def silence_scenario(n: int = 512, filter_length: int = 32, seed: int = 3) -> Scenario:
    """All-zero reference: exercises NLMS energy regularisation."""
    plant = make_plant(filter_length, seed)
    clean = clean_tone(n)
    return Scenario(
        name="silence",
        reference=np.zeros(n),
        desired=clean.copy(),
        clean=clean,
        plants=[plant],
        meta={"seed": seed},
    )


def abrupt_change_scenario(
    n: int = 8000, filter_length: int = 64, seed: int = 11
) -> Scenario:
    """Plant switches to a different response halfway through the stream."""
    plant_a = make_plant(filter_length, seed)
    plant_b = make_plant(filter_length, seed + 100)
    reference = white_noise(n, seed + 1)
    half = n // 2
    noise = np.concatenate(
        [apply_plant(plant_a, reference[:half]), apply_plant(plant_b, reference[half:])]
    )
    clean = clean_tone(n)
    return Scenario(
        name="abrupt_change",
        reference=reference,
        desired=clean + noise,
        clean=clean,
        plants=[plant_a, plant_b],
        meta={"seed": seed, "change_index": half},
    )
