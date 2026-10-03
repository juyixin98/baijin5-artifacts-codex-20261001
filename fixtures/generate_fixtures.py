"""Deterministic local fixtures for the LPC backend tests and demos.

All signals are synthetic and reproducible (fixed seed); no external data
or accounts are involved. The AR process is generated with
``scipy.signal.lfilter`` — deliberately NOT with the application's own
synthesis filter — so the fixtures qualify as independent reference data.

Run:  python -m fixtures.generate_fixtures
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.signal import lfilter

FIXTURE_DIR = Path(__file__).resolve().parent / "data"
SEED = 20260927
SAMPLE_RATE = 16000

# AR(4) poles: two conjugate pairs well inside the unit circle.
AR_POLES = 0.9 * np.exp(1j * 0.5), 0.9 * np.exp(-1j * 0.5), 0.85 * np.exp(1j * 1.1), 0.85 * np.exp(-1j * 1.1)


def _write(name: str, payload: dict) -> Path:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURE_DIR / name
    path.write_text(json.dumps(payload, indent=1))
    return path


def _ar_process() -> dict:
    rng = np.random.default_rng(SEED)
    n = 1600
    true_coefficients = np.poly(np.array(AR_POLES)).real  # [1, a1, ..., a4]
    gain = 0.2
    excitation = gain * rng.standard_normal(n)
    samples = lfilter([1.0], true_coefficients, excitation)
    return {
        "kind": "ar_process",
        "description": "AR(4) process driven by seeded white noise",
        "sample_rate": SAMPLE_RATE,
        "seed": SEED,
        "true_coefficients": true_coefficients.tolist(),
        "true_order": 4,
        "excitation_gain": gain,
        "samples": samples.tolist(),
    }


def _sine() -> dict:
    n = 640
    freq = 440.0
    t = np.arange(n) / SAMPLE_RATE
    samples = 0.5 * np.sin(2.0 * np.pi * freq * t)
    return {
        "kind": "sine",
        "description": "Single 440 Hz sinusoid, amplitude 0.5",
        "sample_rate": SAMPLE_RATE,
        "frequency_hz": freq,
        "amplitude": 0.5,
        "samples": samples.tolist(),
    }


def _silence() -> dict:
    return {
        "kind": "silence",
        "description": "Zero-energy frame (digital silence)",
        "sample_rate": SAMPLE_RATE,
        "samples": np.zeros(320).tolist(),
    }


def _rank_deficient() -> dict:
    """Two sinusoids: effective rank ~4, so a high LPC order is singular."""
    n = 320
    t = np.arange(n) / SAMPLE_RATE
    samples = 0.4 * np.sin(2 * np.pi * 300.0 * t) + 0.3 * np.sin(2 * np.pi * 900.0 * t)
    return {
        "kind": "rank_deficient",
        "description": (
            "Sum of two sinusoids (300 Hz, 900 Hz); the autocorrelation "
            "matrix is numerically singular for high LPC orders"
        ),
        "sample_rate": SAMPLE_RATE,
        "components_hz": [300.0, 900.0],
        "samples": samples.tolist(),
    }


def generate_all() -> list[Path]:
    paths = [
        _write("ar_process.json", _ar_process()),
        _write("sine.json", _sine()),
        _write("silence.json", _silence()),
        _write("rank_deficient.json", _rank_deficient()),
    ]
    return paths


def main() -> None:
    for path in generate_all():
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
