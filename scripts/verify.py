#!/usr/bin/env python3
"""End-to-end verification for the WSOLA backend.

Runs fixed scenarios (tone, impulse train, silence, noise x supported-range
extremes), checks output length / seam continuity / dominant frequency /
impulse period against independent closed-form references, and prints a
report. Exit code is non-zero if any executed check fails.

Checks that cannot be executed in this environment are listed explicitly
under NOT EXECUTED -- they are not reported as passed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fixtures.synth import get_fixture  # noqa: E402
from wsola_backend.contracts import MAX_TIME_SCALE, MIN_TIME_SCALE  # noqa: E402
from wsola_backend.metrics import (  # noqa: E402
    dominant_frequency,
    impulse_spacings,
    seam_report,
    sinusoid_residual_snr_db,
)
from wsola_backend.service import run_stretch  # noqa: E402
from wsola_backend.diagnostics import new_request_id  # noqa: E402

# Thresholds (chosen with margin against measured values; see README).
TONE_FREQ_TOL_HZ = 5.0
TONE_MIN_SNR_DB = 20.0
SEAM_MAX_RATIO = 3.0
IMPULSE_PERIOD_TOL = 4

NOT_EXECUTED = [
    "Subjective listening / MOS evaluation of arbitrary real-world audio "
    "(no artifact-free guarantee is made or verified)",
    "Multi-channel / interleaved stereo input (contract is mono-only)",
    "Long-file memory profile and latency benchmarks",
    "Time scales outside [0.5, 2.0] (rejected by contract, not quality-assessed)",
]


def check(name: str, ok: bool, detail: str, failures: list[str]) -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    if not ok:
        failures.append(name)


def scenario(fixture: str, time_scale: float, failures: list[str]) -> None:
    sr, x = get_fixture(fixture)
    outcome = run_stretch(x, sr, time_scale, request_id=new_request_id(), input_label=fixture)
    result = outcome.result
    n = x.shape[0]
    print(f"scenario={fixture} time_scale={time_scale} decision={outcome.decision.value}")
    check("length", result.output.shape[0] == round(n * time_scale),
          f"target={round(n * time_scale)} actual={result.output.shape[0]}", failures)
    joins = [f.synthesis_pos for f in result.frames[1:]]
    seam = seam_report(result.output, joins)
    check("seam_continuity", seam["worst_ratio"] < SEAM_MAX_RATIO,
          f"worst_ratio={seam['worst_ratio']:.2f} (limit {SEAM_MAX_RATIO})", failures)
    if fixture == "tone_440hz":
        freq = dominant_frequency(result.output, sr)
        check("dominant_frequency", abs(freq - 440.0) <= TONE_FREQ_TOL_HZ,
              f"{freq:.2f} Hz vs 440 Hz (tol {TONE_FREQ_TOL_HZ} Hz)", failures)
        snr = sinusoid_residual_snr_db(result.output, sr, 440.0)
        check("tone_distortion", snr > TONE_MIN_SNR_DB,
              f"residual SNR={snr:.1f} dB (min {TONE_MIN_SNR_DB} dB)", failures)
    if fixture == "impulse_train_100hz":
        spacings = impulse_spacings(result.output)
        med = float(np.median(spacings)) if spacings.size else float("nan")
        check("impulse_period", spacings.size >= 3 and abs(med - 160) <= IMPULSE_PERIOD_TOL,
              f"median spacing={med} samples vs 160 (tol {IMPULSE_PERIOD_TOL})", failures)
    if fixture == "silence":
        check("silence_output", bool(np.all(result.output == 0.0)),
              "output is exactly zero", failures)
        check("silence_decision", outcome.decision.value == "undecidable",
              f"decision={outcome.decision.value}", failures)


def main() -> int:
    failures: list[str] = []
    print(f"supported time_scale range: [{MIN_TIME_SCALE}, {MAX_TIME_SCALE}]")
    for fixture in ("tone_440hz", "impulse_train_100hz", "noise_seeded"):
        for ts in (MIN_TIME_SCALE, 0.8, 1.3, MAX_TIME_SCALE):
            scenario(fixture, ts, failures)
    scenario("silence", 1.5, failures)

    print("\nNOT EXECUTED (not counted as passed):")
    for item in NOT_EXECUTED:
        print(f"  [----] {item}")

    if failures:
        print(f"\nRESULT: FAIL ({len(failures)} checks failed: {failures})")
        return 1
    print("\nRESULT: PASS (all executed checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
