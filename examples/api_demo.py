"""Service-call example: posts a fixture file to a running server.

Start the server first:
    uvicorn limiter.api:app --port 8000

Then:
    python examples/api_demo.py [fixture_id] [base_url]
"""

import json
import sys
from pathlib import Path

import httpx
import numpy as np

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def main(fixture_id: str = "impulse", base_url: str = "http://127.0.0.1:8000") -> None:
    pcm = np.load(FIXTURES / f"{fixture_id}.npz")["pcm"]

    health = httpx.get(f"{base_url}/v1/health").json()
    print("health:", health)

    # --- one-shot offline limiting (latency-compensated) ---
    resp = httpx.post(
        f"{base_url}/v1/limit",
        json={"config": {"threshold": 0.5}, "pcm": pcm.tolist()},
        timeout=60.0,
    )
    resp.raise_for_status()
    body = resp.json()
    print("offline metrics:", json.dumps(body["metrics"], indent=2))

    # --- streaming session over the same signal, two blocks + flush ---
    sid = httpx.post(f"{base_url}/v1/sessions", json={"config": {}}).json()["session_id"]
    half = len(pcm) // 2
    emitted = []
    for block in (pcm[:half], pcm[half:]):
        r = httpx.post(f"{base_url}/v1/sessions/{sid}/blocks",
                       json={"pcm": block.tolist()}, timeout=60.0)
        r.raise_for_status()
        emitted.append(r.json()["emitted"])
    tail = httpx.post(f"{base_url}/v1/sessions/{sid}/flush").json()
    emitted.append(tail["emitted"])
    print(f"streamed emitted per call: {emitted} (sum={sum(emitted)}, input={len(pcm)})")

    # --- error path: NaN is rejected with a category, never a silent 200 ---
    bad = httpx.post(
        f"{base_url}/v1/limit",
        content=b'{"pcm": [[0.0, 0.0], [NaN, 0.0]]}',
        headers={"content-type": "application/json"},
    )
    print("nan payload ->", bad.status_code, bad.json())


if __name__ == "__main__":
    main(*(sys.argv[1:] or []))
