"""Local demo: synthetic signal -> batch vs streaming vs HTTP API.

Runs entirely locally (TestClient, no server process needed) and exits
non-zero if any consistency check fails.

    python scripts/demo.py
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mfcc_backend import MFCCConfig, StreamingMFCC, extract_features  # noqa: E402
from mfcc_backend.contracts import versions  # noqa: E402


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")
    return ok


def main() -> int:
    run_id = uuid.uuid4().hex[:12]
    print(f"demo run_id={run_id}")
    print("versions:", json.dumps(versions()))

    sr = 16000
    cfg = MFCCConfig(sample_rate=sr)
    t = np.arange(sr) / sr
    signal = 0.6 * np.sin(2 * np.pi * 440.0 * t) + 0.05 * np.random.default_rng(0).standard_normal(sr)
    print(f"input: 1s synthetic 440Hz sine + noise @ {sr} Hz ({len(signal)} samples)")

    ok = True

    print("\n[1] batch extraction")
    batch = extract_features(signal, cfg)
    print(f"  frames={batch.n_frames} mfcc{batch.mfcc.shape} "
          f"delta{batch.delta.shape} delta2{batch.delta_delta.shape}")
    ok &= check("frame count", batch.n_frames == 98, f"(got {batch.n_frames}, want 98)")

    print("\n[2] streaming with odd chunk sizes (1, 333, 777, rest)")
    stream = StreamingMFCC(cfg)
    blocks = []
    for lo, hi in [(0, 1), (1, 334), (334, 1111), (1111, len(signal))]:
        emit = stream.accept_chunk(signal[lo:hi])
        blocks.append(emit.mfcc)
        print(f"  chunk [{lo}:{hi}] -> emitted {emit.n_frames} frames "
              f"(total {stream.frames_emitted})")
    final = stream.finalize()
    blocks.append(final.mfcc)
    print(f"  finalize -> emitted {final.n_frames} frames (total {stream.frames_emitted})")
    streamed = np.vstack(blocks)
    ok &= check("stream == batch (mfcc)",
                np.allclose(streamed, batch.mfcc, atol=1e-9, rtol=0),
                f"max_err={np.max(np.abs(streamed - batch.mfcc)):.3e}")

    print("\n[3] HTTP API via TestClient (in-process, no server needed)")
    from fastapi.testclient import TestClient

    from mfcc_backend.api import app

    client = TestClient(app)
    resp = client.post("/v1/mfcc", json={"samples": signal.tolist(), "sample_rate": sr})
    body = resp.json()
    ok &= check("POST /v1/mfcc", resp.status_code == 200 and body["n_frames"] == 98,
                f"(status={resp.status_code}, run_id={body.get('run_id')})")
    ok &= check(
        "API == library",
        np.allclose(np.array(body["mfcc"]["values"]), batch.mfcc, atol=1e-9, rtol=0),
    )

    print("\n[4] error semantics")
    # httpx will not serialize NaN via json=; send the raw JSON body.
    nan_samples = "[0.0, NaN, " + ", ".join(["0.01"] * 598) + "]"
    bad = client.post("/v1/mfcc",
                      content=f'{{"samples": {nan_samples}, "sample_rate": {sr}}}',
                      headers={"content-type": "application/json"})
    ok &= check("NaN -> 400 input_contract",
                bad.status_code == 400
                and bad.json()["error"]["category"] == "input_contract",
                f"(status={bad.status_code})")
    short = client.post("/v1/mfcc", json={"samples": [0.1] * 100, "sample_rate": sr})
    ok &= check("short input -> 200, n_frames=0 + warning",
                short.status_code == 200 and short.json()["n_frames"] == 0
                and len(short.json()["warnings"]) > 0)

    print(f"\ndemo run_id={run_id} result: {'ALL CHECKS PASSED' if ok else 'FAILURES'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
