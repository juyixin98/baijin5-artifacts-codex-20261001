#!/usr/bin/env python3
"""Local demo: synthesize a binary raster with Pillow, run the exact EDT
through the service (in-process via FastAPI's TestClient — no server
needed), cross-check distances against SciPy, and save a heatmap.

Usage:
    python scripts/demo.py [--tiles 64] [--out demo_output]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

import numpy as np
from PIL import Image, ImageDraw
from fastapi.testclient import TestClient
from scipy.ndimage import distance_transform_edt

from edt_service.api import app


def synthesize_mask(size: int = 256) -> np.ndarray:
    """Draw a few shapes; nonzero pixels become sources."""
    img = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(img)
    draw.ellipse([size // 8, size // 8, size // 4, size // 4], fill=255)
    draw.rectangle([size // 2, size // 3, size // 2 + 12, 2 * size // 3], fill=255)
    draw.polygon(
        [(3 * size // 4, size // 5), (7 * size // 8, size // 2), (5 * size // 8, size // 2)],
        fill=255,
    )
    return np.asarray(img) > 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tiles", type=int, default=0, help="force tile size (0 = direct)")
    parser.add_argument("--spacing", type=float, nargs=2, default=[1.0, 1.0], metavar=("DY", "DX"))
    parser.add_argument("--out", type=Path, default=Path("demo_output"))
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    mask = synthesize_mask()
    Image.fromarray((mask * 255).astype(np.uint8)).save(args.out / "mask.png")

    payload = {
        "grid": mask.astype(int).tolist(),
        "spacing": list(args.spacing),
        "request_id": "demo-local-run",
    }
    if args.tiles > 0:
        payload["tile_size"] = args.tiles

    client = TestClient(app)
    resp = client.post("/v1/edt", json=payload)
    resp.raise_for_status()
    body = resp.json()

    print(f"request_id : {body['request_id']} (header: {resp.headers['x-request-id']})")
    print(f"mode       : {body['mode']}  tiles: {len(body['tiles'])}")
    print(f"elapsed_ms : {body['elapsed_ms']}")
    print(f"versions   : {json.dumps(body['versions'])}")

    dist = np.array(
        [[np.inf if v is None else v for v in row] for row in body["distances"]]
    )
    labels = np.array(
        [[-1 if v is None else v for v in row] for row in body["labels"]]
    )

    # Independent cross-check: distances must match SciPy exactly.
    expected = distance_transform_edt(~mask, sampling=tuple(args.spacing))
    max_abs_err = float(np.abs(dist - expected).max())
    print(f"scipy cross-check max |err|: {max_abs_err:.3e}")
    assert max_abs_err < 1e-9, "demo cross-check failed"

    # Label sanity: every label points at a real source, and the distance
    # to that source equals the reported distance.
    h, w = mask.shape
    src_r, src_c = labels // w, labels % w
    dy, dx = args.spacing
    yy, xx = np.indices(mask.shape)
    recomputed = np.sqrt(((yy - src_r) * dy) ** 2 + ((xx - src_c) * dx) ** 2)
    assert mask[src_r, src_c].all(), "label points at a non-source pixel"
    assert np.allclose(recomputed, dist), "label/distance mismatch"
    print("label sanity : every label is a source at the reported distance")

    finite = np.isfinite(dist)
    heat = np.zeros_like(dist, dtype=np.uint8)
    if finite.any():
        peak = dist[finite].max()
        heat[finite] = (dist[finite] / peak * 255).astype(np.uint8) if peak else 0
    Image.fromarray(heat).save(args.out / "distance_heatmap.png")
    print(f"artifacts    : {args.out}/mask.png, {args.out}/distance_heatmap.png")
    print(f"max distance : {float(dist[finite].max()):.3f} (physical units)")


if __name__ == "__main__":
    main()
