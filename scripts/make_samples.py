"""Generate the sample binary images under data/.

Usage: python3 scripts/make_samples.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.conftest import (  # noqa: E402
    cross_tile_stroke_image,
    fork_image,
    ring_image,
    thin_bridge_image,
)

OUT = Path(__file__).resolve().parent.parent / "data"

SAMPLES = {
    "ring.png": ring_image,
    "thin_bridge.png": thin_bridge_image,
    "fork.png": fork_image,
    "cross_tile_stroke.png": cross_tile_stroke_image,
}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, builder in SAMPLES.items():
        arr = builder()
        Image.fromarray(arr * 255).save(OUT / name)
        print(f"wrote {OUT / name} shape={arr.shape} foreground={int(arr.sum())}")


if __name__ == "__main__":
    main()
