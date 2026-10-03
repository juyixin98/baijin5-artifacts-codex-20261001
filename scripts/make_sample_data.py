"""Generate the committed sample fixtures under ``data/``.

Produces:
- ``data/circle_64.png``        64x64 grayscale synthetic image (bright
                                circle + Gaussian noise, fixed seed);
- ``data/sample_request.json``  a ready-to-POST /v1/segment request that
                                references the PNG via base64 and includes
                                foreground/background hard seeds.

Run:  python scripts/make_sample_data.py
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SIZE = 64
FG_MEAN, BG_MEAN, NOISE_SIGMA = 200.0, 60.0, 15.0
MODEL_SIGMA = 30.0
POTTS_WEIGHT = 1.5
RNG_SEED = 42


def main() -> None:
    rng = np.random.default_rng(RNG_SEED)
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    circle = (yy - SIZE / 2) ** 2 + (xx - SIZE / 2) ** 2 <= 18 ** 2
    image = np.where(circle, FG_MEAN, BG_MEAN)
    image = image + rng.normal(0.0, NOISE_SIGMA, size=image.shape)
    image = np.clip(image, 0, 255).astype(np.uint8)

    DATA_DIR.mkdir(exist_ok=True)
    png_path = DATA_DIR / "circle_64.png"
    Image.fromarray(image, mode="L").save(png_path, format="PNG")

    buf = io.BytesIO()
    Image.fromarray(image, mode="L").save(buf, format="PNG")
    payload = base64.b64encode(buf.getvalue()).decode("ascii")

    seeds = (
        [{"row": int(r), "col": int(c), "label": 1}
         for r, c in [(32, 32), (28, 30), (36, 34)]]
        + [{"row": int(r), "col": int(c), "label": 0}
           for r, c in [(2, 2), (61, 61), (2, 61), (61, 2)]]
    )
    request = {
        "image": {"format": "png_base64", "data": payload},
        "data_model": {"type": "intensity_quadratic",
                       "fg_mean": FG_MEAN, "bg_mean": BG_MEAN,
                       "sigma": MODEL_SIGMA},
        "pairwise": {"type": "potts", "weight": POTTS_WEIGHT},
        "seeds": seeds,
    }
    req_path = DATA_DIR / "sample_request.json"
    req_path.write_text(json.dumps(request, indent=2), encoding="utf-8")

    print(f"wrote {png_path} ({png_path.stat().st_size} bytes)")
    print(f"wrote {req_path} ({req_path.stat().st_size} bytes)")
    print(f"image: {SIZE}x{SIZE}, fg_mean={FG_MEAN}, bg_mean={BG_MEAN}, "
          f"noise_sigma={NOISE_SIGMA}, rng_seed={RNG_SEED}, seeds={len(seeds)}")


if __name__ == "__main__":
    main()
