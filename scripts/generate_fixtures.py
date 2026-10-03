"""Generate the local synthetic fixtures used by the test-suite.

Everything is deterministic (fixed seeds) and purely local — no external
data, no accounts. Re-run to regenerate:

    python scripts/generate_fixtures.py

Fixtures:
  ar_signal.npz  AR(4) process with known generating coefficients
  sine.npz       single 440 Hz tone at fs=8000
  silence.npz    all-zero frames (zero-energy definition check)
  noise.npz      white noise (dense-spectrum cross-check material)
  expected.json  reference answers NOT produced by the core under test
                 (closed-form sine LPC, AR generating coefficients)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.signal import lfilter

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
FS = 8000
SEED = 507


def make_ar_signal() -> tuple[np.ndarray, np.ndarray]:
    """AR(4) process: poles at 0.9*exp(+-j*pi/3), 0.7*exp(+-j*2pi/3).

    Chosen so all four generating coefficients are non-trivial:
    a_true = [1, -0.2, 0.67, 0.126, 0.3969].
    """
    poles = np.array(
        [
            0.9 * np.exp(1j * np.pi / 3),
            0.9 * np.exp(-1j * np.pi / 3),
            0.7 * np.exp(1j * 2 * np.pi / 3),
            0.7 * np.exp(-1j * 2 * np.pi / 3),
        ]
    )
    a_true = np.poly(poles).real  # [1, a1, a2, a3, a4]
    rng = np.random.default_rng(SEED)
    excitation = rng.standard_normal(8192)
    x = lfilter([1.0], a_true, excitation)
    # drop startup transient so the process is stationary
    return x[1024:].astype(np.float64), a_true


def make_sine() -> np.ndarray:
    n = np.arange(1024)
    return 0.5 * np.sin(2 * np.pi * 440.0 / FS * n)


def make_silence() -> np.ndarray:
    return np.zeros(512)


def make_noise() -> np.ndarray:
    rng = np.random.default_rng(SEED + 1)
    return 0.3 * rng.standard_normal(2048)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    ar, a_true = make_ar_signal()
    np.savez(OUT / "ar_signal.npz", samples=ar, a_true=a_true, fs=FS)

    sine = make_sine()
    np.savez(OUT / "sine.npz", samples=sine, freq=440.0, fs=FS)

    np.savez(OUT / "silence.npz", samples=make_silence(), fs=FS)
    np.savez(OUT / "noise.npz", samples=make_noise(), fs=FS)

    # Reference answers derived analytically, independent of the core:
    # a pure sustained sinusoid is exactly predictable by the order-2
    # predictor [1, -2*cos(w), 1].
    w = 2 * np.pi * 440.0 / FS
    expected = {
        "fs": FS,
        "seed": SEED,
        "sine": {
            "freq_hz": 440.0,
            "order2_lpc_closed_form": [1.0, float(-2 * np.cos(w)), 1.0],
            "closed_form_tolerance": 0.05,
        },
        "ar": {
            "a_true": a_true.tolist(),
            "recovery_tolerance": 0.15,
        },
        "autocorr_handcheck": {
            "signal": [1.0, 2.0, 3.0, 4.0],
            "order": 2,
            "r": [30.0, 20.0, 11.0],
        },
    }
    (OUT / "expected.json").write_text(json.dumps(expected, indent=2))
    print(f"fixtures written to {OUT}")
    print(f"AR(4) true coefficients: {a_true.tolist()}")


if __name__ == "__main__":
    main()
