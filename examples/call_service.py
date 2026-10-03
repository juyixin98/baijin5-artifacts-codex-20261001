"""Minimal service-call example (requires the server to be running).

Start the server first:
    uvicorn seamcarve.api:app --port 8000

Then:
    python3 examples/call_service.py
"""

from __future__ import annotations

import json

import httpx

BASE = "http://127.0.0.1:8000"

# hand_4x4 fixture: the hand-derived minimal seam is column 0, energy 120
PIXELS = [
    [10, 20, 30, 40],
    [10, 20, 30, 40],
    [50, 60, 70, 80],
    [50, 60, 70, 80],
]


def main() -> None:
    with httpx.Client(base_url=BASE, timeout=10.0) as client:
        print("== GET /v1/version ==")
        print(json.dumps(client.get("/v1/version").json(), indent=2))

        print("\n== POST /v1/seam/find (gradient) ==")
        resp = client.post("/v1/seam/find", json={"pixels": PIXELS})
        print(resp.status_code, json.dumps(resp.json(), indent=2))

        print("\n== POST /v1/carve (2 seams, chunk_size=1) ==")
        resp = client.post(
            "/v1/carve", json={"pixels": PIXELS, "n_seams": 2, "chunk_size": 1}
        )
        print(resp.status_code, json.dumps(resp.json(), indent=2))

        print("\n== POST /v1/seam/find with a fully protected row (expect 409) ==")
        mask = [[0, 0, 0, 0], [0, 0, 0, 0], [1, 1, 1, 1], [0, 0, 0, 0]]
        resp = client.post(
            "/v1/seam/find", json={"pixels": PIXELS, "protect_mask": mask}
        )
        print(resp.status_code, json.dumps(resp.json(), indent=2))


if __name__ == "__main__":
    main()
