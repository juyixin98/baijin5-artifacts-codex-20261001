"""Example client for the FIR estimation backend.

Generates a synthetic known-FIR fixture locally (white excitation, delay 5,
light noise), calls the one-shot endpoint, and prints the recovered
coefficients next to the truth plus the run-log id for replay.

Usage:
    uvicorn app.main:app --port 8000   # in another shell
    python3 examples/example_client.py
"""

from __future__ import annotations

import json
import os

import httpx
import numpy as np

BASE = os.environ.get("FIR_BASE_URL", "http://127.0.0.1:8000")
TRUE_FIR = [1.0, -0.55, 0.3, 0.15, -0.08, 0.04]


def main() -> None:
    rng = np.random.default_rng(20261003)
    n = 4000
    delay = 5
    x = rng.standard_normal(n)
    clean = np.convolve(x, TRUE_FIR)[:n]
    y = np.concatenate([np.zeros(delay), clean[:-delay]])
    y = y + 0.02 * rng.standard_normal(n)

    request = {
        "excitation": x.tolist(),
        "response": y.tolist(),
        "model_order": len(TRUE_FIR),
        "regularization": 1e-6,
        "estimate_delay": True,
        "boundary": "valid",
        "holdout_fraction": 0.25,
    }
    with httpx.Client(base_url=BASE, timeout=30.0) as client:
        resp = client.post("/v1/fir/estimate", json=request)
        resp.raise_for_status()
        body = resp.json()

    print(f"run_id:            {body['run_id']}")
    print(f"delay used:        {body['delay']} ({body['delay_source']})")
    print(f"identifiable:      {body['identifiable']} "
          f"(rank {body['identifiability']['rank']}/{body['identifiability']['n_columns']}, "
          f"cond {body['identifiability']['condition_number']:.2e})")
    print(f"train rmse:        {body['train_metrics']['rmse']:.6f}")
    print(f"holdout rmse:      {body['holdout_metrics']['rmse']:.6f}")
    print("coefficients (estimated vs true):")
    for k, (est, true) in enumerate(zip(body["coefficients"], TRUE_FIR)):
        print(f"  h[{k}] = {est:+.6f}   ({true:+.6f})")
    print("\nwarnings:", json.dumps(body["warnings"]))


if __name__ == "__main__":
    main()
