"""Generate the minimal committed test fixtures (deterministic, no network).

Every fixture is a tiny grayscale PNG whose expected seam behaviour is
hand-derived in the test-suite.  Regenerate with::

    python scripts/make_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def _save(name: str, arr: np.ndarray) -> Path:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURE_DIR / name
    Image.fromarray(arr.astype(np.uint8), mode="L").save(path)
    return path


def main() -> None:
    # Rows constant => vertical gradient is zero; only column steps matter.
    # Zero-energy columns (Sobel, reflect borders): {0, 1, 4, 5}.
    _save("step_5x6.png", np.tile(np.array([[10, 10, 10, 200, 200, 200]]), (5, 1)))

    # Single bright spike column; after its removal the remainder is flat,
    # so the *recomputed* energy of the next seam is 0 (stale-energy check).
    _save("spike_4x3.png", np.tile(np.array([[0, 255, 0]]), (4, 1)))

    # Uniform image: every seam has energy 0 (tie-break must pick col 0).
    _save("flat_4x4.png", np.full((4, 4), 128))

    # Two disjoint zero-energy columns {0, 3}: tied optimal seams,
    # deterministic tie-break must pick the leftmost (col 0).
    _save("tied_4x4.png", np.tile(np.array([[10, 10, 200, 200]]), (4, 1)))

    # Hand-computed forward-energy case (see tests/test_energy.py).
    _save("forward_2x3.png", np.array([[0, 0, 0], [9, 0, 9]]))

    # Protection masks (255 = protected).
    mask_row = np.zeros((5, 6), dtype=np.uint8)
    mask_row[2, :] = 255  # entire row protected => no legal vertical seam
    _save("mask_row_5x6.png", mask_row)
    _save("mask_full_5x6.png", np.full((5, 6), 255, dtype=np.uint8))

    print(f"fixtures written to {FIXTURE_DIR}")


if __name__ == "__main__":
    main()
