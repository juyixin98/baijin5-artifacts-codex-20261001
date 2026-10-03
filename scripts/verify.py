#!/usr/bin/env python3
"""Standalone verification battery for the partitioned convolution backend.

Runs the reviewable checks end to end and prints a PASS/FAIL table with the
observed error magnitudes.  References are independent of the engine under
test (NumPy direct convolution, pure-Python convolution, SciPy fftconvolve).

Usage:
    python scripts/verify.py            # engine + stream level checks
    python scripts/verify.py --url http://127.0.0.1:8000   # also check a live server

Exit code is 0 only if every check passes.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.convolution import SwapStrategy
from app.fixtures import (
    make_exponential_ir,
    make_impulse,
    make_long_ir,
    make_lowpass_ir,
    make_random_signal,
)
from app.stream import StreamSession
from tests.reference import direct_convolve_np, direct_convolve_py, fft_convolve_scipy

TOL = 1e-8


@dataclass
class Results:
    rows: list[tuple[str, bool, str]] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name:<48} {detail}")

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.rows)


def run_stream(x, ir, block_size, strategy=SwapStrategy.CROSSFADE, fade=4):
    session = StreamSession(
        session_id="verify", sample_rate=48_000, block_size=block_size,
        ir=ir, swap_strategy=strategy, crossfade_blocks=fade,
    )
    outs = []
    for start in range(0, len(x), block_size):
        chunk = x[start : start + block_size]
        outs.append(session.push_block(chunk, final=start + block_size >= len(x)))
    outs.append(session.flush())
    return np.concatenate(outs)


def max_err(a, b) -> float:
    return float(np.max(np.abs(np.asarray(a) - np.asarray(b)))) if len(a) == len(b) else float("inf")


def engine_checks(results: Results) -> None:
    # 1. Impulse -> IR identity.
    ir = make_exponential_ir(500, seed=3)
    y = run_stream(make_impulse(300), ir, 128)
    results.check(
        "impulse reproduces IR (length + values)",
        len(y) == 300 + 500 - 1 and max_err(y[:500], ir) < TOL,
        f"len={len(y)} max_err={max_err(y[:500], ir):.3e}",
    )

    # 2. Random signal vs NumPy direct convolution.
    ir = make_lowpass_ir(257, cutoff=0.2)
    x = make_random_signal(4096, seed=42)
    y = run_stream(x, ir, 256)
    ref = direct_convolve_np(x, ir)
    results.check("random signal vs numpy.convolve", max_err(y, ref) < TOL, f"max_err={max_err(y, ref):.3e}")

    # 3. Small case vs pure-Python reference.
    rng = np.random.Generator(np.random.PCG64(5))
    ir_s, x_s = rng.standard_normal(13), rng.standard_normal(29)
    y = run_stream(x_s, ir_s, 8)
    results.check(
        "small case vs pure-python reference",
        max_err(y, direct_convolve_py(x_s, ir_s)) < 1e-12,
        f"max_err={max_err(y, direct_convolve_py(x_s, ir_s)):.3e}",
    )

    # 4. SciPy fftconvolve cross-check.
    ir = make_exponential_ir(1000, seed=17)
    x = make_random_signal(2048, seed=18)
    y = run_stream(x, ir, 512)
    results.check(
        "vs scipy.signal.fftconvolve",
        max_err(y, fft_convolve_scipy(x, ir)) < TOL,
        f"max_err={max_err(y, fft_convolve_scipy(x, ir)):.3e}",
    )

    # 5. Block-size invariance.
    ir = make_exponential_ir(700, seed=23)
    x = make_random_signal(1500, seed=24)
    outs = [run_stream(x, ir, b) for b in (64, 128, 256, 1024)]
    worst = max(max_err(outs[0], o) for o in outs[1:])
    same_len = all(len(o) == len(outs[0]) for o in outs)
    results.check("block-size invariance (64..1024)", same_len and worst < 1e-12, f"max_pairwise_err={worst:.3e}")

    # 6. Non-integral tail.
    ir = make_exponential_ir(400, seed=31)
    x = make_random_signal(3 * 256 + 37, seed=32)
    y = run_stream(x, ir, 256)
    ref = direct_convolve_np(x, ir)
    results.check(
        "non-integral tail (3*256+37 samples)",
        len(y) == len(ref) and max_err(y, ref) < TOL,
        f"len={len(y)} expected={len(ref)} max_err={max_err(y, ref):.3e}",
    )

    # 7. Ultra-long IR.
    ir = make_long_ir(100_000, seed=99)
    x = make_random_signal(20_000, seed=100)
    y = run_stream(x, ir, 1024)
    ref = direct_convolve_np(x, ir)
    results.check(
        "ultra-long IR (100k taps)",
        len(y) == len(ref) and max_err(y, ref) < 1e-7,
        f"len={len(y)} max_err={max_err(y, ref):.3e}",
    )

    # 8. Tail fully flushed (last L-1 samples carry real energy and match).
    ir = make_exponential_ir(300, seed=41)
    x = make_random_signal(512, seed=43)
    y = run_stream(x, ir, 128)
    ref = direct_convolve_np(x, ir)
    tail = y[len(x):]
    results.check(
        "tail flush matches reference",
        len(tail) == len(ir) - 1 and max_err(tail, ref[len(x):]) < TOL and np.max(np.abs(tail)) > 1e-3,
        f"tail_len={len(tail)} max_err={max_err(tail, ref[len(x):]):.3e}",
    )

    # 9. Crossfade transparency under silent guard.
    ir = make_exponential_ir(256, seed=81)
    x = np.concatenate([make_random_signal(512, seed=82), np.zeros(1024), make_random_signal(512, seed=83)])
    ref = run_stream(x, ir, 128)
    s = StreamSession(session_id="verify-xf", sample_rate=48_000, block_size=128,
                      ir=ir, swap_strategy=SwapStrategy.CROSSFADE, crossfade_blocks=4)
    parts = []
    for start in range(0, len(x), 128):
        if start == 768:
            s.swap_ir(ir, SwapStrategy.CROSSFADE)
        chunk = x[start:start + 128]
        parts.append(s.push_block(chunk, final=start + 128 >= len(x)))
    parts.append(s.flush())
    out = np.concatenate(parts)
    results.check(
        "crossfade transparency (silent guard, same IR)",
        max_err(out, ref) < TOL,
        f"max_err={max_err(out, ref):.3e}",
    )


def server_checks(results: Results, base_url: str) -> None:
    def post(path, payload):
        req = urllib.request.Request(
            base_url + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())

    ir = make_exponential_ir(300, seed=101)
    status, body = post("/v1/sessions", {
        "sample_rate": 48000, "block_size": 128, "ir": ir.tolist(), "swap_strategy": "restart",
    })
    results.check("server: create session", status == 201, f"status={status}")
    if status != 201:
        return
    sid = body["session_id"]

    x = make_random_signal(1000, seed=105)
    outs = []
    ok = True
    for start in range(0, len(x), 128):
        chunk = x[start:start + 128]
        status, body = post(f"/v1/sessions/{sid}/blocks",
                            {"samples": chunk.tolist(), "final": start + 128 >= len(x)})
        ok = ok and status == 200
        outs.append(body["output"])
    status, flush = post(f"/v1/sessions/{sid}/flush", {})
    y = np.concatenate([np.asarray(o) for o in outs] + [np.asarray(flush["tail"])])
    ref = direct_convolve_np(x, ir)
    results.check(
        "server: stream matches direct convolution",
        ok and status == 200 and len(y) == len(ref) and max_err(y, ref) < TOL,
        f"len={len(y)} max_err={max_err(y, ref):.3e}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="also run checks against a live server at this base URL")
    args = parser.parse_args()

    results = Results()
    engine_checks(results)
    if args.url:
        server_checks(results, args.url.rstrip("/"))

    failed = [name for name, ok, _ in results.rows if not ok]
    print(f"\n{len(results.rows) - len(failed)}/{len(results.rows)} checks passed")
    if failed:
        print("failed:", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
