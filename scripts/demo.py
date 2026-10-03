#!/usr/bin/env python3
"""Local demo: batch pipeline on synthetic fixtures, streaming equivalence,
and error semantics — end to end against the real FastAPI app (in-process
TestClient, no server or network needed).

Usage:  python scripts/demo.py
Exit code 0 only if every step passes; failures are printed, never hidden.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from mfcc_backend import fixtures
from mfcc_backend.config import MFCCConfig
from mfcc_backend.pipeline import compute_pipeline
from mfcc_backend.service import app, versions
from mfcc_backend.streaming import StreamingMFCC

PASS, FAIL = "PASS", "FAIL"
failures = 0


def report(step: str, ok: bool, detail: str) -> None:
    global failures
    print(f"[{PASS if ok else FAIL}] {step}: {detail}")
    if not ok:
        failures += 1


def main() -> int:
    print("== versions ==")
    print(json.dumps(versions(), indent=2))

    cfg = MFCCConfig().validate()
    print(f"\n== fixed config ==\n{json.dumps(cfg.as_dict(), indent=2)}")

    # 1. batch pipeline on every fixture
    print("\n== batch pipeline on synthetic fixtures ==")
    results = {}
    for name in ("sine_440", "white_noise", "silence", "one_frame"):
        x = fixtures.FIXTURES[name]()
        res = compute_pipeline(x, cfg)
        results[name] = res
        sha = hashlib.sha1(np.asarray(x, dtype=np.float64).tobytes()).hexdigest()[:16]
        report(
            f"batch:{name}",
            res.mfcc.shape == (res.n_frames, 13) and np.all(np.isfinite(res.mfcc)),
            f"input n={len(x)} sha1={sha} frames={res.n_frames} "
            f"mfcc{res.mfcc.shape} delta{res.delta.shape} delta2{res.delta2.shape}",
        )

    # silence is fully determined by the log floor
    sil = results["silence"]
    expected_c0 = float(np.sqrt(cfg.n_mels) * np.log(cfg.log_floor))
    report(
        "silence determinism",
        np.allclose(sil.mfcc[:, 0], expected_c0, atol=1e-12)
        and np.allclose(sil.mfcc[:, 1:], 0.0, atol=1e-12),
        f"c0={sil.mfcc[0, 0]:.6f} expected={expected_c0:.6f}, c1..=0",
    )

    # 2. streaming equals batch
    print("\n== streaming vs batch ==")
    x = fixtures.make_white_noise(duration_s=0.3, seed=5)
    batch = compute_pipeline(x, cfg)
    stream = StreamingMFCC(cfg)
    got = {"mfcc": [], "delta": [], "delta2": []}
    pos = 0
    for size in (500, 160, 1200, 777, 4000):
        chunk = x[pos : pos + size]
        pos += size
        if not len(chunk):
            continue
        em = stream.accept_chunk(chunk)
        for k in got:
            got[k].append(em.__dict__[k])
    em = stream.finish()
    for k in got:
        got[k].append(em.__dict__[k])
    for k, ref in (("mfcc", batch.mfcc), ("delta", batch.delta), ("delta2", batch.delta2)):
        arr = np.concatenate([b for b in got[k] if len(b)], axis=0)
        report(f"stream==batch:{k}", np.allclose(arr, ref, rtol=0, atol=1e-12),
               f"shape={arr.shape} max|diff|={float(np.max(np.abs(arr - ref))):.3e}")

    # 3. error semantics through the HTTP API
    print("\n== API error semantics (must be non-200, structured) ==")
    client = TestClient(app)
    cases = [
        ("too_short", {"sample_rate": 16000, "samples": [0.1] * 100}, 422, "INSUFFICIENT_SIGNAL"),
        ("bad_config", {"sample_rate": 16000, "samples": fixtures.make_sine(duration_s=0.1).tolist(),
                        "config": {"n_mfcc": 30}}, 422, "INVALID_CONFIG"),
        ("empty_filter_support",
         {"sample_rate": 8000, "samples": fixtures.make_sine(duration_s=0.1).tolist(),
          "config": {"frame_length_ms": 1.0, "hop_length_ms": 0.5}}, 422, "EMPTY_FILTER_SUPPORT"),
    ]
    for name, payload, status, code in cases:
        r = client.post("/v1/features", json=payload)
        body = r.json()
        ok = r.status_code == status and body.get("error", {}).get("code") == code
        report(f"api_error:{name}", ok,
               f"http={r.status_code} code={body.get('error', {}).get('code')}")

    # NaN cannot be serialised by httpx's strict encoder; send the raw body.
    nan_body = '{"sample_rate": 16000, "samples": [' + "0.0, NaN, 1.0," * 200 + "0.5]}"
    r = client.post("/v1/features", content=nan_body,
                    headers={"content-type": "application/json"})
    body = r.json()
    report("api_error:nan_input",
           r.status_code == 400 and body.get("error", {}).get("code") == "INVALID_AUDIO",
           f"http={r.status_code} code={body.get('error', {}).get('code')}")

    # 4. happy-path API round trip
    r = client.post("/v1/features",
                    json={"sample_rate": 16000, "samples": fixtures.make_sine(duration_s=0.1).tolist()})
    body = r.json()
    report("api_features", r.status_code == 200 and body["meta"]["n_frames"] == 8,
           f"http={r.status_code} request_id={body.get('request_id')} "
           f"frames={body['meta']['n_frames']} (1 + (1600-400)//160 = 8)")

    print(f"\n== summary: {'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'} ==")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
