"""Synthetic local fixtures: clean signal, correlated/decorrelated reference
noise, silence windows and abrupt plant changes.

Everything is seeded and computed locally — no external data. The known
clean signal and the true noise-path coefficients are returned alongside
the mixtures so evaluation can be done against ground truth (never against
"output energy went down").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

# True noise-path plant used by the correlated-noise scenarios.
TRUE_PLANT = np.array([0.9, -0.4, 0.2, 0.1], dtype=np.float64)
# Plant after an abrupt change (same length, clearly different).
TRUE_PLANT_AFTER_CHANGE = np.array([-0.6, 0.3, 0.35, -0.2], dtype=np.float64)
# Reference-path filter. The reference channel observes the noise source
# directly (identity path) so the optimal canceller is *exactly* the FIR
# TRUE_PLANT — this makes the coefficient-error metric well-defined and
# lets tests verify convergence against known coefficients rather than
# against an approximate Wiener solution.
REFERENCE_PATH = np.array([1.0], dtype=np.float64)


@dataclass
class Scenario:
    name: str
    clean: np.ndarray
    primary: np.ndarray  # clean + noise (what the "microphone" hears)
    reference: np.ndarray  # noise reference fed to the adaptive filter
    true_coeffs: np.ndarray  # plant the filter should identify
    meta: dict[str, Any] = field(default_factory=dict)


def make_clean_signal(n: int, seed: int) -> np.ndarray:
    """Deterministic multi-sinusoid 'speech-like' clean signal."""
    t = np.arange(n, dtype=np.float64)
    rng = np.random.default_rng(seed + 1)
    phase = rng.uniform(0.0, 2.0 * np.pi, size=3)
    signal = (
        0.6 * np.sin(2.0 * np.pi * 0.013 * t + phase[0])
        + 0.3 * np.sin(2.0 * np.pi * 0.041 * t + phase[1])
        + 0.15 * np.sin(2.0 * np.pi * 0.097 * t + phase[2])
    )
    return signal


def _filter(signal: np.ndarray, taps: np.ndarray) -> np.ndarray:
    """FIR filtering via convolution (fixtures only; tests cross-check with
    scipy.signal.lfilter so the reference answer is independently derived)."""
    return np.convolve(signal, taps)[: signal.shape[0]]


def correlated_noise_scenario(n: int, seed: int) -> Scenario:
    """Reference correlated with primary noise: the case LMS/NLMS solves."""
    rng = np.random.default_rng(seed)
    noise_source = rng.standard_normal(n)
    primary_noise = _filter(noise_source, TRUE_PLANT)
    reference = _filter(noise_source, REFERENCE_PATH)
    clean = make_clean_signal(n, seed)
    return Scenario(
        name="correlated_noise",
        clean=clean,
        primary=clean + primary_noise,
        reference=reference,
        true_coeffs=TRUE_PLANT.copy(),
        meta={"seed": seed, "noise_path": TRUE_PLANT.tolist()},
    )


def decorrelated_reference_scenario(n: int, seed: int) -> Scenario:
    """Reference statistically independent of the primary noise.

    Expected failure mode: cancellation cannot succeed, SNR improvement
    stays near or below 0 dB. Included deliberately to demonstrate the
    applicability boundary.
    """
    rng = np.random.default_rng(seed)
    noise_source = rng.standard_normal(n)
    primary_noise = _filter(noise_source, TRUE_PLANT)
    reference = np.random.default_rng(seed + 7).standard_normal(n)  # independent
    clean = make_clean_signal(n, seed)
    return Scenario(
        name="decorrelated_reference",
        clean=clean,
        primary=clean + primary_noise,
        reference=reference,
        true_coeffs=TRUE_PLANT.copy(),
        meta={"seed": seed, "expectation": "cancellation_fails"},
    )


def silence_scenario(n: int, seed: int, silence: tuple[int, int] | None = None) -> Scenario:
    """Correlated-noise scenario with a forced-silent reference window.

    During silence the reference is exactly zero; NLMS regularization must
    prevent division by zero and the weights must not move.
    """
    scenario = correlated_noise_scenario(n, seed)
    start, end = silence if silence is not None else (n // 3, 2 * n // 3)
    reference = scenario.reference.copy()
    reference[start:end] = 0.0
    return Scenario(
        name="silence",
        clean=scenario.clean,
        primary=scenario.primary,
        reference=reference,
        true_coeffs=scenario.true_coeffs,
        meta={"seed": seed, "silence_interval": [start, end]},
    )


def abrupt_change_scenario(n: int, seed: int, change_at: int | None = None) -> Scenario:
    """Noise path switches coefficients mid-stream; filter must reconverge."""
    rng = np.random.default_rng(seed)
    change = change_at if change_at is not None else n // 2
    noise_source = rng.standard_normal(n)
    first = _filter(noise_source[:change], TRUE_PLANT)
    second = _filter(noise_source[change:], TRUE_PLANT_AFTER_CHANGE)
    primary_noise = np.concatenate([first, second])
    reference = _filter(noise_source, REFERENCE_PATH)
    clean = make_clean_signal(n, seed)
    return Scenario(
        name="abrupt_change",
        clean=clean,
        primary=clean + primary_noise,
        reference=reference,
        true_coeffs=TRUE_PLANT_AFTER_CHANGE.copy(),
        meta={
            "seed": seed,
            "change_at": change,
            "plant_before": TRUE_PLANT.tolist(),
            "plant_after": TRUE_PLANT_AFTER_CHANGE.tolist(),
        },
    )


SCENARIOS = {
    "correlated_noise": correlated_noise_scenario,
    "decorrelated_reference": decorrelated_reference_scenario,
    "silence": silence_scenario,
    "abrupt_change": abrupt_change_scenario,
}


def build_scenario(name: str, n: int, seed: int) -> Scenario:
    if name not in SCENARIOS:
        from app.errors import InputValidationError

        raise InputValidationError(
            "unknown scenario",
            detail={"scenario": name, "available": sorted(SCENARIOS)},
        )
    return SCENARIOS[name](n, seed)
