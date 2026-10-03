"""Minimal service-call example (in-process, no network needed).

For a real HTTP run see docs/REPRODUCE.md (uvicorn + curl). This script
posts the sustained_peaks fixture to the FastAPI app via TestClient and
saves the response summary to docs/service_example_output.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from limiter.api import app

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    pcm = np.load(ROOT / "fixtures" / "sustained_peaks.npz")["pcm"]
    config = json.loads((ROOT / "config" / "limiter.default.json").read_text())
    payload = {
        "config": config,
        "channels": pcm.T.tolist(),
        "block_size": 512,
        "run_id": "example-sustained",
    }
    client = TestClient(app)
    resp = client.post("/v1/limit", json=payload)
    resp.raise_for_status()
    body = resp.json()

    out = np.asarray(body["output"])
    summary = {
        "endpoint": "POST /v1/limit",
        "run_id": body["run_id"],
        "latency_samples": body["latency_samples"],
        "input_samples": int(pcm.shape[0]),
        "output_samples": int(out.shape[1]),
        "input_peak": float(np.max(np.abs(pcm))),
        "output_peak": float(np.max(np.abs(out))),
        "threshold_linear": body["stats"]["threshold_linear"],
        "min_gain": body["stats"]["min_gain"],
        "sample_peak_ceiling_ok": body["stats"]["sample_peak_ceiling_ok"],
    }
    (ROOT / "docs" / "service_example_output.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True)
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
