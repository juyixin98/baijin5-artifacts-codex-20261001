"""Generate deterministic synthetic PCM fixtures (no external data).

Each fixture is a ``fixtures/<name>.npz`` with key ``pcm`` (n, 2) float64
plus a ``fixtures/<name>.json`` sidecar describing how and why it was built.
Re-running this script regenerates byte-identical files (fixed seeds).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

FS = 8000.0
SEED = 20260927
OUT = Path(__file__).resolve().parent


def save(name: str, pcm: np.ndarray, meta: dict) -> None:
    pcm = np.asarray(pcm, dtype=np.float64)
    np.savez(OUT / f"{name}.npz", pcm=pcm)
    meta = {"sample_rate": FS, "num_samples": int(pcm.shape[0]), **meta}
    (OUT / f"{name}.json").write_text(json.dumps(meta, indent=2, sort_keys=True))
    print(f"wrote {name}: {pcm.shape}")


def stereo(mono: np.ndarray) -> np.ndarray:
    return np.column_stack([mono, mono])


def main() -> None:
    n = np.arange(4096)

    # 1. Single short impulse well above threshold, surrounded by silence.
    pcm = np.zeros((2048, 2))
    pcm[1000, :] = 0.95
    save(
        "short_impulse",
        pcm,
        {
            "description": "single 0.95 impulse at sample 1000 in silence; "
            "verifies latency, attack anticipation and release recovery",
            "impulse_index": 1000,
            "impulse_amplitude": 0.95,
        },
    )

    # 2. Stereo imbalance: left 0.9, right 0.18 (ratio exactly 0.2).
    left = 0.9 * np.sin(2 * np.pi * 440.0 * n / FS)
    right = 0.18 * np.sin(2 * np.pi * 440.0 * n / FS)
    save(
        "stereo_imbalance",
        np.column_stack([left, right]),
        {
            "description": "left 0.9 / right 0.18 sine; verifies linked gain "
            "preserves the inter-channel ratio and both channels stay under ceiling",
            "channel_ratio": 0.2,
        },
    )

    # 3. Sustained over-threshold peaks: constant 0.95 sine.
    pcm = stereo(0.95 * np.sin(2 * np.pi * 330.0 * np.arange(8192) / FS))
    save(
        "sustained_peaks",
        pcm,
        {
            "description": "continuous 0.95 sine; verifies steady-state gain "
            "converges to threshold/amplitude and ceiling holds indefinitely",
            "amplitude": 0.95,
        },
    )

    # 4. Burst straddling a 512-sample block boundary.
    rng = np.random.default_rng(SEED)
    pcm = 0.05 * rng.standard_normal((4096, 2))
    burst = 0.9 * np.sin(2 * np.pi * 1000.0 * np.arange(16) / FS)
    pcm[504:520, :] += burst[:, None]
    pcm = np.clip(pcm, -1.0, 1.0)
    save(
        "block_boundary_burst",
        pcm,
        {
            "description": "0.9 sine burst spanning samples [504, 520) across the "
            "512 block boundary on a 0.05 noise floor; verifies block-independent "
            "gain and ceiling at chunk edges",
            "burst_range": [504, 520],
            "block_size": 512,
            "seed": SEED,
        },
    )

    # 5. Impulse followed by long silence to observe release recovery.
    pcm = np.zeros((2048, 2))
    pcm[256, :] = 0.95
    save(
        "gain_recovery",
        pcm,
        {
            "description": "0.95 impulse at sample 256 then silence; verifies the "
            "gain recovers at the configured release rate",
            "impulse_index": 256,
            "impulse_amplitude": 0.95,
        },
    )

    # 6. Inter-sample peak content: fs/4 sine at 45-degree phase; sample peak
    # ~0.39 but reconstructed true peak 0.55 (above a -6 dBFS threshold).
    phase = np.pi / 4.0
    pcm = stereo(0.55 * np.sin(2 * np.pi * (FS / 4.0) * np.arange(2048) / FS + phase))
    save(
        "true_peak_rich",
        pcm,
        {
            "description": "fs/4 sine at pi/4 phase; sample peak ~0.389 stays under "
            "threshold while reconstructed true peak 0.55 exceeds it; discriminates "
            "sample-peak vs true-peak limiting",
            "amplitude": 0.55,
            "frequency_hz": FS / 4.0,
            "phase_rad": phase,
        },
    )


if __name__ == "__main__":
    main()
