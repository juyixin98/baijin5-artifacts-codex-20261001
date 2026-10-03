"""Deterministic synthetic fixtures.

All fixtures are local and synthetic: no external services, no real
business data. Reference FIR taps are hand-specified literal vectors
(not derived from the estimator), and responses are synthesized with
``numpy.convolve`` — an independent path from the estimator's Toeplitz
matrix — so tests compare the core against outside references.

Fixture kinds:

- "clean":      white-noise excitation, noiseless response
- "noisy":      white-noise excitation, additive Gaussian output noise
- "narrowband": near-sinusoidal excitation (spectrally degenerate)
- "delayed":    white excitation, response lagging by a known delay
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Hand-specified reference channels. "lowpass4" is a literal vector;
# "decay8" is an exponential decay defined by its formula, not by the
# estimator under test.
REFERENCE_FIR: dict[str, np.ndarray] = {
    "lowpass4": np.array([0.4, 0.3, 0.2, 0.1]),
    "decay8": 0.9 * np.exp(-0.35 * np.arange(8)),
}


@dataclass(frozen=True)
class Fixture:
    excitation: np.ndarray
    response: np.ndarray
    true_coefficients: np.ndarray
    delay: int
    noise_std: float
    kind: str


def white_excitation(n_samples: int, *, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal(n_samples)


def narrowband_excitation(
    n_samples: int, *, seed: int, cycles: float = 3.0, jitter: float = 1e-6
) -> np.ndarray:
    """Near-pure sinusoid: spectrally degenerate by construction."""
    rng = np.random.default_rng(seed)
    t = np.arange(n_samples)
    carrier = np.sin(2.0 * np.pi * cycles * t / n_samples)
    return carrier + jitter * rng.standard_normal(n_samples)


def synthesize_response(
    excitation: np.ndarray,
    coefficients: np.ndarray,
    *,
    noise_std: float = 0.0,
    delay: int = 0,
    seed: int = 0,
) -> np.ndarray:
    """response[t] = conv(excitation, h)[t - delay] + noise.

    Positive delay means the response lags the excitation (the first
    `delay` response samples are zeros shifted in from before the
    observation window).
    """
    clean = np.convolve(excitation, coefficients, mode="full")[: excitation.shape[0]]
    if delay > 0:
        clean = np.concatenate([np.zeros(delay), clean])[: excitation.shape[0]]
    if noise_std > 0:
        rng = np.random.default_rng(seed)
        clean = clean + noise_std * rng.standard_normal(excitation.shape[0])
    return clean


def make_fixture(
    kind: str,
    *,
    n_samples: int = 512,
    seed: int = 20261003,
    fir: str = "decay8",
    noise_std: float = 0.05,
    delay: int = 5,
) -> Fixture:
    """Build one of the named fixtures. Raises KeyError for unknown kinds."""
    h = REFERENCE_FIR[fir]
    if kind == "clean":
        x = white_excitation(n_samples, seed=seed)
        y = synthesize_response(x, h)
        return Fixture(x, y, h, delay=0, noise_std=0.0, kind=kind)
    if kind == "noisy":
        x = white_excitation(n_samples, seed=seed)
        y = synthesize_response(x, h, noise_std=noise_std, seed=seed + 1)
        return Fixture(x, y, h, delay=0, noise_std=noise_std, kind=kind)
    if kind == "narrowband":
        # Pure sinusoid: the design matrix has formal rank 2, so the
        # identifiability diagnostic must fire.
        x = narrowband_excitation(n_samples, seed=seed, jitter=0.0)
        y = synthesize_response(x, h)
        return Fixture(x, y, h, delay=0, noise_std=0.0, kind=kind)
    if kind == "delayed":
        x = white_excitation(n_samples, seed=seed)
        y = synthesize_response(x, h, delay=delay)
        return Fixture(x, y, h, delay=delay, noise_std=0.0, kind=kind)
    raise KeyError(f"unknown fixture kind {kind!r}")
