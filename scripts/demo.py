#!/usr/bin/env python3
"""Local demonstration of the exact EDT service — no external accounts.

Runs entirely in-process by default (FastAPI via TestClient) so no server
process is needed. Pass ``--url http://127.0.0.1:8000`` to exercise a
running server instead.

Fixtures are synthesized locally:

1. tiny tie case (sources equidistant from the centre)
2. anisotropic pixel pitch (sy=2.0, sx=0.5)
3. long-thin raster (1 x 41)
4. no sources / all sources
5. larger sparse raster forced through the tiled path

Each case prints concrete distances and nearest-source coordinates plus
failure/warning categories and the request id used.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def mask_to_png_b64(mask: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(mask.astype(np.uint8) * 255, mode="L").save(
        buf, format="PNG"
    )
    return base64.b64encode(buf.getvalue()).decode()


def fixture_cases():
    # 1. Exact tie in a 5-pixel row.
    m = np.zeros((1, 5), dtype=bool)
    m[0, 0] = m[0, 4] = True
    yield "equidistant-tie", m, 1.0, 1.0, False

    # 2. Non-square pixels.
    m = np.zeros((4, 6), dtype=bool)
    m[0, 0] = True
    yield "anisotropic-pixels", m, 2.0, 0.5, False

    # 3. Long-thin image.
    m = np.zeros((1, 41), dtype=bool)
    m[0, 0] = m[0, 40] = True
    yield "long-thin", m, 1.0, 1.0, False

    # 4a. No sources.
    yield "no-sources", np.zeros((3, 4), dtype=bool), 1.0, 1.0, False
    # 4b. All sources.
    yield "all-sources", np.ones((3, 4), dtype=bool), 1.0, 1.0, False

    # 5. Sparse larger raster forced through tiling.
    rng = np.random.default_rng(0)
    m = rng.random((120, 80)) < 0.01
    yield "large-sparse-tiled", m, 1.0, 1.0, True


def print_case(title, body):
    print(f"\n=== {title} ===")
    print(f"request_id     : {body['request_id']}")
    print(f"kernel_version : {body['kernel_version']}")
    print(f"spacing        : sy={body['spacing_y']} sx={body['spacing_x']}")
    print("stats          :", json.dumps(body["stats"]))
    print("execution      :", json.dumps(body["execution"]))
    if body["distance_grid"] is not None:
        grid = body["distance_grid"]
        shown = [[None if v is None else round(v, 4) for v in row]
                 for row in grid]
        print("distance_grid  :", json.dumps(shown))
        print("nearest_y_grid :", json.dumps(body["nearest_y_grid"]))
        print("nearest_x_grid :", json.dumps(body["nearest_x_grid"]))
    else:
        print("(JSON grids omitted for large raster; decode the PNG fields)")
    print("failures       :", json.dumps(body["failures"]))
    print("warnings       :", json.dumps(body["warnings"]))


def run_inprocess():
    from fastapi.testclient import TestClient
    from app.api import app

    with TestClient(app) as client:
        for title, mask, sy, sx, force in fixture_cases():
            resp = client.post("/v1/edt/verify", json={
                "image_base64": mask_to_png_b64(mask),
                "spacing_y": sy,
                "spacing_x": sx,
                "force_tiled": force,
                "request_id": f"demo-{title}",
            })
            print_case(title, resp.json())


def run_http(base_url: str):
    import httpx

    with httpx.Client(base_url=base_url, timeout=60.0) as client:
        for title, mask, sy, sx, force in fixture_cases():
            resp = client.post("/v1/edt/verify", json={
                "image_base64": mask_to_png_b64(mask),
                "spacing_y": sy,
                "spacing_x": sx,
                "force_tiled": force,
                "request_id": f"demo-{title}",
            })
            resp.raise_for_status()
            print_case(title, resp.json())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", help="Use a running server, e.g. "
                                  "http://127.0.0.1:8000")
    args = ap.parse_args()
    t0 = time.perf_counter()
    if args.url:
        run_http(args.url.rstrip("/"))
    else:
        run_inprocess()
    print(f"\nDemo finished in {time.perf_counter() - t0:.2f}s.")


if __name__ == "__main__":
    main()
