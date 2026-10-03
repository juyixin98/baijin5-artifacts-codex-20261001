#!/usr/bin/env python3
"""End-to-end API walkthrough using only the Python standard library.

Assumes the server is running locally, e.g.:

    ./scripts/run_dev.sh

It performs:
  1. a validation check for an odd window/hop pair,
  2. a batch STFT -> ISTFT round trip,
  3. a frame-wise streaming analyze -> synthesize round trip.

Run:  python3 examples/example_roundtrip.py [base_url]
"""

from __future__ import annotations

import json
import math
import sys
import urllib.error
import urllib.request
import uuid


def call(base_url: str, path: str, payload: dict, method: str = "POST") -> dict:
    request_id = uuid.uuid4().hex
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Request-ID": request_id,
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8"))
    print(f"-> {path}  (request_id={body.get('request_id')})")
    if not body.get("success"):
        error = body["error"]
        print("   FAILED:", error["code"], "at stage", error["stage"])
        print("   ", error["message"])
        sys.exit(1)
    return body["data"]


def main(base_url: str = "http://127.0.0.1:8000") -> None:
    # 1. Odd window length validation.
    validation = call(base_url, "/v1/validate", {
        "nperseg": 5, "hop": 2, "window": "hann",
    })
    diag = validation["diagnostics"]
    print(
        "   reconstructable =", validation["reconstructable"],
        "| min OLA denominator =", f"{diag['min_denominator']:.4f}",
    )

    # 2. Batch round trip, including a boundary pulse.
    signal = [1.0] + [0.0] * 31
    result = call(base_url, "/v1/roundtrip", {
        "signal": signal, "nperseg": 8, "hop": 3,
    })
    err = result["error"]
    print(
        "   batch round trip: n_frames =", result["n_frames"],
        "| max_abs_error =", f"{err['max_abs_error']:.2e}",
        "| length_preserved =", err["length_preserved"],
    )

    # 3. Streaming analyze -> synthesize.
    created = call(base_url, "/v1/streams", {
        "direction": "analyze", "nperseg": 8, "hop": 3,
    })
    sid = created["session_id"]
    rng_signal = [math.sin(0.5 * i) for i in range(40)]
    frames = []
    first = call(base_url, f"/v1/streams/{sid}/analyze",
                 {"samples": rng_signal[:15]})
    frames.extend(first["frames"])
    second = call(base_url, f"/v1/streams/{sid}/analyze",
                  {"samples": rng_signal[15:], "finish": True})
    frames.extend(second["frames"])
    print(f"   streamed {len(frames)} analysis frames in two chunks")

    synth = call(base_url, "/v1/streams", {
        "direction": "synthesize", "nperseg": 8, "hop": 3,
    })
    synth_id = synth["session_id"]
    for frame in frames[:-1]:
        call(base_url, f"/v1/streams/{synth_id}/synthesize", {
            "frame_index": frame["frame_index"], "bins": frame["bins"],
        })
    final = call(base_url, f"/v1/streams/{synth_id}/synthesize", {
        "frame_index": frames[-1]["frame_index"],
        "bins": frames[-1]["bins"],
        "finish": True,
        "signal_length": len(rng_signal),
    })
    max_error = max(abs(a - b) for a, b in zip(rng_signal, final["signal"]))
    print(
        "   streaming round trip: length =", final["length"],
        "| max_abs_error =", f"{max_error:.2e}",
    )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000")
