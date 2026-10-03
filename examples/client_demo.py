#!/usr/bin/env python3
"""End-to-end demo against a locally running service.

Usage:
    uvicorn app.main:app --port 8000 &
    python3 examples/client_demo.py
"""

import json
import os
import urllib.request

import numpy as np
import scipy.signal

BASE = os.environ.get("SOSFILT_BASE_URL", "http://127.0.0.1:8000")


def call(method, path, payload=None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def main():
    # 4th-order Butterworth low-pass at 0.2 * Nyquist, as SOS rows.
    sos = scipy.signal.butter(4, 0.2, output="sos")

    status, stream = call("POST", "/streams", {
        "sample_rate": 48000.0,
        "num_channels": 2,
        "coefficients": sos.tolist(),
        "initial_condition": "zero",
    })
    print("create:", status, stream)
    sid = stream["stream_id"]

    rng = np.random.default_rng(42)
    for i in range(3):  # chunked processing, pinned to param version 1
        block = rng.standard_normal((128, 2))
        status, out = call("POST", f"/streams/{sid}/blocks", {
            "samples": block.tolist(),
            "param_version": stream["param_version"],
        })
        print(f"block {i}:", status, "samples_processed =",
              out["samples_processed"])

    # switch coefficients mid-stream (state reset), version 1 -> 2
    new_sos = scipy.signal.butter(2, 0.35, output="sos")
    status, upd = call("PUT", f"/streams/{sid}/coefficients", {
        "coefficients": new_sos.tolist(),
        "expected_version": 1,
        "transient": "reset_state",
    })
    print("update:", status, upd)

    # a stale client pinning the old version is rejected with 409
    status, err = call("POST", f"/streams/{sid}/blocks", {
        "samples": [[0.0, 0.0]],
        "param_version": 1,
    })
    print("stale write:", status, err["error"]["category"])

    status, _ = call("DELETE", f"/streams/{sid}")
    print("delete:", status)


if __name__ == "__main__":
    main()
