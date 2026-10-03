#!/usr/bin/env python3
"""Generate the local synthetic fixtures under ``data/``.

Fixtures (all deterministic, seeded RNG — no external data):
    sample_circle.png   64x64 grayscale: bright disk on dark background
                        plus Gaussian noise
    sample_seeds.json   hard seeds: foreground pixels inside the disk,
                        background pixels in the corners
    sample_spec.json    a ready-to-POST request body for /v1/segment using
                        the image_model unary + contrast pairwise modes
    sample_ground_truth.npy  the noise-free disk mask used by the
                        integration test to score IoU
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SIZE = 64
DISK_CENTER = (32, 32)
DISK_RADIUS = 16
FG_LEVEL = 0.8
BG_LEVEL = 0.2
NOISE_SIGMA = 0.08
RNG_SEED = 20261003


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(RNG_SEED)

    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    disk = (yy - DISK_CENTER[0]) ** 2 + (xx - DISK_CENTER[1]) ** 2 \
        <= DISK_RADIUS**2
    clean = np.where(disk, FG_LEVEL, BG_LEVEL)
    noisy = np.clip(clean + rng.normal(0.0, NOISE_SIGMA, clean.shape), 0.0, 1.0)

    Image.fromarray((noisy * 255).round().astype(np.uint8), mode="L").save(
        DATA_DIR / "sample_circle.png"
    )
    np.save(DATA_DIR / "sample_ground_truth.npy", disk.astype(np.int8))

    fg_seeds = [
        int(r * SIZE + c)
        for r, c in ((32, 32), (28, 32), (32, 28), (36, 36))
    ]
    bg_seeds = [
        int(r * SIZE + c)
        for r, c in ((2, 2), (2, 61), (61, 2), (61, 61))
    ]
    seeds = {"foreground": fg_seeds, "background": bg_seeds}
    (DATA_DIR / "sample_seeds.json").write_text(json.dumps(seeds, indent=2))

    spec = {
        "image_id": "sample_circle.png",
        "width": SIZE,
        "height": SIZE,
        "unary": {
            "mode": "image_model",
            "fg_mean": FG_LEVEL,
            "bg_mean": BG_LEVEL,
            "sigma": NOISE_SIGMA,
        },
        "pairwise": {"mode": "contrast", "weight": 2.0, "beta": 30.0},
        "seeds": seeds,
    }
    (DATA_DIR / "sample_spec.json").write_text(json.dumps(spec, indent=2))

    print(f"wrote fixtures to {DATA_DIR}")
    for path in sorted(DATA_DIR.iterdir()):
        print(f"  {path.name} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
