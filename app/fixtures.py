"""Deterministic synthetic fixtures — no external data, no real audio.

All generators are seeded and pure, so tests, the verification script, and
ad-hoc reproduction produce bit-identical signals.  ``scripts/make_fixtures.py``
can dump these to ``fixtures/*.npz`` for reuse outside Python.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import firwin


def make_impulse(n: int, amplitude: float = 1.0, index: int = 0) -> np.ndarray:
    """Unit impulse: convolution with it must reproduce the IR exactly."""
    x = np.zeros(n, dtype=np.float64)
    x[index] = amplitude
    return x


def make_random_signal(n: int, seed: int = 1234) -> np.ndarray:
    """White Gaussian signal from an explicit PCG64 stream."""
    return np.random.Generator(np.random.PCG64(seed)).standard_normal(n)


def make_exponential_ir(length: int, seed: int = 7, decay: float | None = None) -> np.ndarray:
    """Reverb-like IR: shaped noise with exponential decay."""
    tau = decay if decay is not None else length / 6.0
    rng = np.random.Generator(np.random.PCG64(seed))
    t = np.arange(length, dtype=np.float64)
    return rng.standard_normal(length) * np.exp(-t / tau)


def make_lowpass_ir(length: int, cutoff: float = 0.25) -> np.ndarray:
    """Deterministic FIR lowpass IR via windowed-sinc design (SciPy)."""
    if length % 2 == 0:
        length += 1  # firwin type-I requires odd length
    return firwin(length, cutoff).astype(np.float64)


def make_long_ir(length: int = 100_000, seed: int = 99) -> np.ndarray:
    """Ultra-long IR fixture for partitioned-convolution stress tests."""
    return make_exponential_ir(length, seed=seed)


def fixture_bundle() -> dict[str, np.ndarray]:
    """Named standard fixtures shared by tests and the verification script."""
    return {
        "impulse_1024": make_impulse(1024),
        "random_4096": make_random_signal(4096, seed=2024),
        "ir_exp_512": make_exponential_ir(512, seed=11),
        "ir_lowpass_257": make_lowpass_ir(257, cutoff=0.2),
        "ir_long_100k": make_long_ir(100_000, seed=99),
    }
