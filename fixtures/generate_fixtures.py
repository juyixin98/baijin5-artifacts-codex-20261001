"""Generate local synthetic fixtures with known ground truth.

Ground truth is produced here, *not* by the kernel under test:

* Integer/sub-pixel shifts are applied with ``scipy.ndimage.shift`` (spline
  resampling in the spatial domain) on a base texture larger than the crop, so
  both images contain genuine content up to their borders.
* The periodic texture is defined analytically.
* The manifest records the shifts and the *expected* result category, which
  the validation endpoint and the test-suite check against.

Images are stored as 16-bit grayscale PNGs (8.8 fixed point of a 0-255 float
range) to keep quantization noise well below the sub-pixel tolerance.

Run:  python fixtures/generate_fixtures.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

SIZE = 128
BASE = 192          # base texture is larger than the crop
CROP0 = 32          # crop origin inside the base texture
OUT_DIR = Path(__file__).resolve().parent / "data"


def _save_png(path: Path, arr: np.ndarray) -> None:
    """Store a float image (0-255 range) as 16-bit grayscale PNG."""
    q = np.clip(np.round(arr * 256.0), 0, 65535).astype(np.uint16)
    Image.fromarray(q, mode="I;16").save(path)


def _base_texture(seed: int, size: int = BASE, sigma: float = 1.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal((size, size))
    smooth = ndimage.gaussian_filter(noise, sigma)
    smooth -= smooth.min()
    return smooth / smooth.max() * 255.0


def _shifted_pair(base: np.ndarray, dy: float, dx: float) -> tuple[np.ndarray, np.ndarray]:
    ref = base[CROP0:CROP0 + SIZE, CROP0:CROP0 + SIZE]
    shifted = ndimage.shift(base, (dy, dx), order=3, mode="nearest", prefilter=True)
    mov = shifted[CROP0:CROP0 + SIZE, CROP0:CROP0 + SIZE]
    return ref, mov


def _sinusoid(dy: float = 0.0, dx: float = 0.0, period: float = 16.0) -> np.ndarray:
    y, x = np.mgrid[0:SIZE, 0:SIZE]
    return (127.5
            + 60.0 * np.sin(2.0 * np.pi * (y - dy) / period)
            + 60.0 * np.sin(2.0 * np.pi * (x - dx) / period))


def generate_all(out_dir: Path = OUT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    fixtures: dict[str, dict] = {}

    def emit(fid: str, ref: np.ndarray, mov: np.ndarray, **meta) -> None:
        _save_png(out_dir / f"{fid}_ref.png", ref)
        _save_png(out_dir / f"{fid}_mov.png", mov)
        fixtures[fid] = {"reference": f"{fid}_ref.png", "moving": f"{fid}_mov.png", **meta}

    # 1. exact integer shift ------------------------------------------------
    ref, mov = _shifted_pair(_base_texture(seed=101), 5.0, -3.0)
    emit("integer_shift", ref, mov, ground_truth_shift=[5.0, -3.0],
         expected_category="ok",
         description="exact integer translation (5, -3)")

    # 2. sub-pixel shift ----------------------------------------------------
    ref, mov = _shifted_pair(_base_texture(seed=102), 2.4, -1.7)
    emit("subpixel_shift", ref, mov, ground_truth_shift=[2.4, -1.7],
         expected_category="ok",
         description="sub-pixel translation (2.4, -1.7)")

    # 3. brightness change on top of a shift --------------------------------
    ref, mov = _shifted_pair(_base_texture(seed=103), 3.25, 4.5)
    mov_b = mov * 1.25 + 18.0
    emit("brightness_change", ref, mov_b, ground_truth_shift=[3.25, 4.5],
         expected_category="ok", applied_gain=1.25, applied_offset=18.0,
         description="shift (3.25, 4.5) plus linear brightness change")

    # 4. periodic texture: ambiguous peaks ----------------------------------
    emit("periodic_texture", _sinusoid(), _sinusoid(4.0, 2.0),
         ground_truth_shift=[4.0, 2.0], period_px=16.0,
         expected_category="ambiguous_peaks",
         description="pure sinusoid, period 16 px: shift ambiguous modulo period")

    # 5. constant image: no phase information --------------------------------
    emit("constant_image", np.full((SIZE, SIZE), 128.0),
         np.full((SIZE, SIZE), 128.0),
         ground_truth_shift=None, expected_category="flat_response",
         description="constant images: correlation surface is uniform")

    # 6. low overlap ---------------------------------------------------------
    ref, mov = _shifted_pair(_base_texture(seed=106, sigma=2.0), 90.0, 0.0)
    emit("low_overlap", ref, mov, ground_truth_shift=[90.0, 0.0],
         expected_category="low_overlap",
         description="shift 90 px on 128 px image: overlap fraction ~0.30")

    # 7. no common content ---------------------------------------------------
    ref = _base_texture(seed=107)
    mov = _base_texture(seed=108)
    emit("no_overlap", ref[CROP0:CROP0 + SIZE, CROP0:CROP0 + SIZE],
         mov[CROP0:CROP0 + SIZE, CROP0:CROP0 + SIZE],
         ground_truth_shift=None, expected_category="no_common_content",
         description="independent textures: any peak is spurious")

    manifest = {
        "generator": "fixtures/generate_fixtures.py",
        "size": SIZE,
        "encoding": "16-bit grayscale PNG, value = round(0-255 float * 256)",
        "fixtures": fixtures,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    m = generate_all()
    print(f"wrote {len(m['fixtures'])} fixtures to {OUT_DIR}", file=sys.stderr)
