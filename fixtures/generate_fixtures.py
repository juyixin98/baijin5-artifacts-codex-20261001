"""Generate the minimal synthetic data fixtures (deterministic, seeded).

Writes PNG images + mask JSON + an index.json with shapes and sha256 into
``fixtures/data/``. Re-running is idempotent: same seeds, same bytes.

Fixtures:
  hand_4x4          hand-computable grayscale ramp (used by hand-derived tests)
  uniform_5x5       constant image -> every seam ties
  grad_8x6          smooth gradient with a vertical high-energy bar
  rgb_10x8          seeded RGB noise smoothed with a Gaussian (scipy)
  protected_row_6x6 seeded noise, row 2 fully protected -> no legal seam
  random_12x10      seeded noise for multi-seam carve tests
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

DATA_DIR = Path(__file__).resolve().parent / "data"

HAND_4x4 = np.array(
    [
        [10, 20, 30, 40],
        [10, 20, 30, 40],
        [50, 60, 70, 80],
        [50, 60, 70, 80],
    ],
    dtype=np.uint8,
)


def _sha256(arr: np.ndarray) -> str:
    h = hashlib.sha256()
    h.update(str(arr.shape).encode())
    h.update(arr.dtype.str.encode())
    h.update(np.ascontiguousarray(arr).tobytes())
    return h.hexdigest()


def _save_png(arr: np.ndarray, path: Path) -> None:
    Image.fromarray(arr).save(path)


def build_fixtures() -> list[dict]:
    fixtures: list[dict] = []

    def add(name: str, image: np.ndarray, description: str, mask: np.ndarray | None = None):
        png = f"{name}.png"
        _save_png(image, DATA_DIR / png)
        entry = {
            "name": name,
            "png": png,
            "shape": list(image.shape),
            "sha256": _sha256(image),
            "description": description,
        }
        if mask is not None:
            mask_file = f"{name}.mask.json"
            (DATA_DIR / mask_file).write_text(json.dumps(mask.astype(int).tolist()))
            entry["mask"] = mask_file
        (DATA_DIR / f"{name}.json").write_text(json.dumps(entry, indent=2))
        fixtures.append(entry)

    add("hand_4x4", HAND_4x4, "hand-computable grayscale ramp; min seam = column 0")

    add("uniform_5x5", np.full((5, 5), 128, dtype=np.uint8),
        "constant image; all seams tie -> deterministic tie-break path")

    grad = np.tile(np.linspace(0, 60, 6, dtype=np.uint8), (8, 1))
    grad[:, 3] = 255  # vertical high-energy bar the seam must avoid
    add("grad_8x6", grad, "smooth gradient + bright vertical bar at column 3")

    rng = np.random.default_rng(20261003)
    rgb = rng.integers(0, 256, size=(10, 8, 3), dtype=np.uint8)
    rgb = np.clip(gaussian_filter(rgb.astype(float), sigma=(1.2, 1.2, 0)), 0, 255).astype(np.uint8)
    add("rgb_10x8", rgb, "seeded RGB noise, Gaussian-smoothed (scipy)")

    rng = np.random.default_rng(7)
    noise6 = rng.integers(0, 256, size=(6, 6), dtype=np.uint8)
    mask6 = np.zeros((6, 6), dtype=bool)
    mask6[2, :] = True  # full protected row -> no legal vertical seam
    add("protected_row_6x6", noise6, "row 2 fully protected", mask=mask6)

    rng = np.random.default_rng(42)
    add("random_12x10", rng.integers(0, 256, size=(12, 10), dtype=np.uint8),
        "seeded noise for multi-seam carve tests")

    return fixtures


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fixtures = build_fixtures()
    index = {"fixtures": fixtures}
    (DATA_DIR / "index.json").write_text(json.dumps(index, indent=2))
    for f in fixtures:
        print(f"{f['name']}: shape={f['shape']} sha256={f['sha256'][:16]}...")


if __name__ == "__main__":
    main()
