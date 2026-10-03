#!/usr/bin/env python3
"""Verification battery for the WSOLA backend.

Runs the synthetic fixtures (fixed pitch, impulse train, silence, noise)
across the quality and extreme rate ranges and checks, independently of
the core under test:

* output length contract  (== round(n / rate), exact)
* seam continuity         (max jump at segment boundaries)
* dominant frequency      (FFT peak vs. fixture parameter)
* distortion              (SNR against an independently fitted sinusoid)
* impulse period          (preserved) and impulse count (scales with rate)
* streaming == offline    (bit-identical)

Exit code 0 iff every executed check passes. Checks that cannot run in
this environment are listed explicitly as NOT RUN -- never as passed.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

from wsola_backend import fixtures, metrics
from wsola_backend.config import (
    HARD_MAX_RATE,
    HARD_MIN_RATE,
    QUALITY_MAX_RATE,
    QUALITY_MIN_RATE,
)
from wsola_backend.stream import WsolaStream
from wsola_backend.wsola import wsola_stretch

SR = fixtures.DEFAULT_SAMPLE_RATE
TONE_FREQ = 440.0
SEAM_JUMP_LIMIT = 0.2
SNR_LIMIT_DB = 20.0
FREQ_TOL_HZ = 3.0


@dataclass
class Report:
    rows: list[dict] = field(default_factory=list)
    not_run: list[dict] = field(default_factory=list)

    def add(self, case: str, check: str, value: str, limit: str, ok: bool):
        self.rows.append(
            {"case": case, "check": check, "value": value, "limit": limit,
             "status": "PASS" if ok else "FAIL"}
        )

    def skip(self, check: str, reason: str):
        self.not_run.append({"check": check, "status": "NOT RUN", "reason": reason})

    @property
    def failed(self) -> list[dict]:
        return [r for r in self.rows if r["status"] == "FAIL"]


def check_tone(report: Report, rate: float) -> None:
    case = f"tone440 rate={rate}"
    x = fixtures.tone(TONE_FREQ, 1.0, SR)
    result = wsola_stretch(x, rate=rate)
    y = result.samples
    target = round(x.shape[0] / rate)
    report.add(case, "length", str(y.shape[0]), f"== {target}",
               y.shape[0] == target)
    freq = metrics.dominant_frequency(y, SR)
    report.add(case, "dominant_freq_hz", f"{freq:.2f}",
               f"|d| < {FREQ_TOL_HZ} Hz vs {TONE_FREQ}",
               abs(freq - TONE_FREQ) < FREQ_TOL_HZ)
    seams = [s.output_position for s in result.segments[1:]]
    jump = metrics.max_seam_jump(y, seams)
    report.add(case, "seam_max_jump", f"{jump:.4f}", f"< {SEAM_JUMP_LIMIT}",
               jump < SEAM_JUMP_LIMIT)
    snr = metrics.sinusoid_fit_snr_db(y, TONE_FREQ, SR)
    report.add(case, "distortion_snr_db", f"{snr:.1f}", f"> {SNR_LIMIT_DB}",
               snr > SNR_LIMIT_DB)
    report.add(case, "drift", f"{result.max_position_drift:.3f}", "<= 0.5",
               result.max_position_drift <= 0.5 + 1e-9)


def check_impulses(report: Report, rate: float) -> None:
    case = f"impulses rate={rate}"
    period, n = 160, 16_000
    x = fixtures.impulse_train(period, n)
    y = wsola_stretch(x, rate=rate).samples
    spacings = metrics.impulse_spacings(y)
    median = float(np.median(spacings)) if spacings.size else float("nan")
    report.add(case, "period_preserved", f"{median:.1f}",
               f"|median-{period}| <= 2",
               spacings.size > 0 and abs(median - period) <= 2.0)
    expected = round(n / rate) / period
    count = spacings.size + 1
    report.add(case, "impulse_count", str(count),
               f"within 15% of {expected:.0f}",
               abs(count - expected) <= 0.15 * expected)


def check_silence(report: Report, rate: float) -> None:
    case = f"silence rate={rate}"
    x = fixtures.silence(8000)
    result = wsola_stretch(x, rate=rate)
    target = round(8000 / rate)
    report.add(case, "length", str(result.samples.shape[0]), f"== {target}",
               result.samples.shape[0] == target)
    report.add(case, "stays_silent", f"max={np.max(np.abs(result.samples)):.2e}",
               "== 0", bool(np.all(result.samples == 0.0)))
    degenerate_ok = all(s.degenerate for s in result.segments[1:]
                        if not s.pinned)
    report.add(case, "degenerate_flagged", str(degenerate_ok), "True",
               degenerate_ok)


def check_streaming(report: Report, rate: float, chunk: int) -> None:
    case = f"stream rate={rate} chunk={chunk}"
    x = fixtures.noise(5000, seed=7)
    offline = wsola_stretch(x, rate=rate)
    stream = WsolaStream(rate=rate)
    parts = [stream.push(x[i:i + chunk]) for i in range(0, x.shape[0], chunk)]
    parts.append(stream.finalize().samples)
    y = np.concatenate(parts)
    report.add(case, "bit_identical_to_offline", str(np.array_equal(y, offline.samples)),
               "True", bool(np.array_equal(y, offline.samples)))


def main() -> int:
    report = Report()

    for rate in [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]:
        check_tone(report, rate)
    for rate in [0.25, 4.0]:  # extreme: length + finiteness only, no quality gate
        case = f"tone440 rate={rate} (extreme)"
        x = fixtures.tone(TONE_FREQ, 1.0, SR)
        y = wsola_stretch(x, rate=rate).samples
        target = round(x.shape[0] / rate)
        report.add(case, "length", str(y.shape[0]), f"== {target}",
                   y.shape[0] == target)
        report.add(case, "finite", str(bool(np.all(np.isfinite(y)))), "True",
                   bool(np.all(np.isfinite(y))))
    for rate in [0.5, 2.0]:
        check_impulses(report, rate)
    for rate in [0.5, 1.7]:
        check_silence(report, rate)
    for rate in [0.75, 1.3]:
        for chunk in [1, 13, 512, 5000]:
            check_streaming(report, rate, chunk)

    report.skip(
        "perceptual listening evaluation (MUSHRA/ABX)",
        "requires human listeners; not automatable in this environment",
    )
    report.skip(
        "real-world audio corpus regression",
        "policy: local synthetic fixtures only, no external datasets",
    )
    report.skip(
        "long-duration soak (>10M samples via HTTP)",
        "covered at unit level by streaming/offline equivalence instead",
    )

    width = max(len(r["case"]) for r in report.rows)
    print(f"\n{'CASE'.ljust(width)}  {'CHECK'.ljust(26)} {'VALUE'.ljust(10)} "
          f"{'LIMIT'.ljust(24)} STATUS")
    for r in report.rows:
        print(f"{r['case'].ljust(width)}  {r['check'].ljust(26)} "
              f"{r['value'].ljust(10)} {r['limit'].ljust(24)} {r['status']}")
    print(f"\nsupported rate range: hard [{HARD_MIN_RATE}, {HARD_MAX_RATE}], "
          f"guaranteed quality [{QUALITY_MIN_RATE}, {QUALITY_MAX_RATE}]")
    print("distortion is reported per case above; WSOLA makes no "
          "artifact-free guarantee, especially outside the quality range")
    if report.not_run:
        print("\nNOT RUN (explicitly not claimed as passed):")
        for item in report.not_run:
            print(f"  - {item['check']}: {item['reason']}")

    summary = {
        "total": len(report.rows),
        "passed": len(report.rows) - len(report.failed),
        "failed": len(report.failed),
        "not_run": len(report.not_run),
    }
    print(f"\nsummary: {json.dumps(summary)}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
