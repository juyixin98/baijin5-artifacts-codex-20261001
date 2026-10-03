"""Validation interface: run every fixture through the kernel, cross-check
against the independent NCC reference and the construction ground truth,
and emit a JSON report with concrete pass/fail checks per fixture.

Runnable as ``python -m app.validation`` (writes reports/validation_report.json)
and via the HTTP endpoint ``POST /v1/validation/run``.
"""

from __future__ import annotations

import json
import math
import platform
import time
from pathlib import Path

import numpy as np
import PIL
import scipy

from app.config import KernelConfig, get_settings
from app.fixtures import Fixture, load_fixtures
from app.kernel.pipeline import estimate_shift
from app.kernel.reference import ncc_reference
from app.logging_setup import get_logger
from app.version import __version__

log = get_logger("validation")


def _status_matches(actual: str, expected: str) -> bool:
    if expected == "not_ok":
        return actual != "ok"
    return actual == expected


def validate_fixture(fx: Fixture, cfg: KernelConfig) -> dict:
    t0 = time.perf_counter()
    est = estimate_shift(fx.img_a, fx.img_b, cfg)
    checks: list[dict] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    check(
        "status_as_expected",
        _status_matches(est.status, fx.expected_status),
        f"expected {fx.expected_status!r}, got {est.status!r}",
    )
    if fx.expected_status != "ok":
        # Contract: a non-ok verdict must always name its reason(s).
        check(
            "reasons_listed",
            bool(est.failures or est.uncertainties),
            f"failures={est.failures} uncertainties={est.uncertainties}",
        )
    reasons = set(est.failures) | {u.split(":")[0] for u in est.uncertainties}
    for reason in fx.expected_reasons:
        check(f"reason:{reason}", reason in reasons, f"reasons present: {sorted(reasons)}")

    localization_error = None
    if fx.ground_truth_shift is not None and est.shift is not None:
        localization_error = math.hypot(
            est.shift[0] - fx.ground_truth_shift[0],
            est.shift[1] - fx.ground_truth_shift[1],
        )
        if fx.tolerance_px is not None:
            check(
                "localization_within_tolerance",
                localization_error <= fx.tolerance_px,
                f"error {localization_error:.4f}px vs tolerance {fx.tolerance_px}px",
            )

    reference_entry = None
    if fx.reference_max_shift is not None:
        ref = ncc_reference(fx.img_a, fx.img_b, fx.reference_max_shift)
        reference_entry = {
            "best_shift": ref.best_shift,
            "best_score": round(ref.best_score, 5),
            "evaluated_shifts": ref.evaluated,
        }
        if fx.ground_truth_shift is not None and ref.best_shift is not None:
            gt_int = (
                int(round(fx.ground_truth_shift[0])),
                int(round(fx.ground_truth_shift[1])),
            )
            check(
                "reference_matches_ground_truth",
                ref.best_shift == gt_int,
                f"reference {ref.best_shift} vs ground truth {gt_int}",
            )
        if est.status == "ok" and est.integer_shift is not None and ref.best_shift is not None:
            # Only a confident kernel is required to agree with the
            # reference; an uncertain/failed one has already said so.
            check(
                "kernel_matches_reference",
                tuple(est.integer_shift) == tuple(ref.best_shift),
                f"kernel {est.integer_shift} vs reference {ref.best_shift}",
            )

    entry = {
        "name": fx.name,
        "category": fx.category,
        "note": fx.note,
        "expected_status": fx.expected_status,
        "status": est.status,
        "shift": est.shift,
        "confidence": est.confidence,
        "psr": est.psr,
        "overlap_fraction": est.overlap_fraction,
        "failures": est.failures,
        "uncertainties": est.uncertainties,
        "ambiguity_peaks": [
            {"dy": p.dy, "dx": p.dx, "value": round(p.value, 5)} for p in est.peaks
        ],
        "ground_truth_shift": fx.ground_truth_shift,
        "localization_error_px": (
            round(localization_error, 4) if localization_error is not None else None
        ),
        "reference": reference_entry,
        "checks": checks,
        "passed": all(c["passed"] for c in checks),
        "elapsed_ms": round((time.perf_counter() - t0) * 1e3, 1),
    }
    log.info(
        "fixture %s: status=%s passed=%s error=%s",
        fx.name,
        est.status,
        entry["passed"],
        entry["localization_error_px"],
    )
    return entry


def run_validation(fixtures_dir: Path | None = None, cfg: KernelConfig | None = None) -> dict:
    settings = get_settings()
    fixtures_dir = fixtures_dir or settings.fixtures_dir
    cfg = cfg or settings.kernel
    fixtures = load_fixtures(fixtures_dir)

    entries = [validate_fixture(fx, cfg) for fx in fixtures]
    passed = sum(1 for e in entries if e["passed"])
    report = {
        "app_version": __version__,
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "pillow": PIL.__version__,
        },
        "config": {
            "window": cfg.window,
            "pad_factor": cfg.pad_factor,
            "eps_ratio": cfg.eps_ratio,
            "psr_failure": cfg.psr_failure,
            "psr_ok": cfg.psr_ok,
            "ambiguity_ratio": cfg.ambiguity_ratio,
            "min_overlap": cfg.min_overlap,
        },
        "convention": "img_b[y, x] ~= img_a[y - dy, x - dx]",
        "fixtures": entries,
        "summary": {
            "total": len(entries),
            "passed": passed,
            "failed": len(entries) - passed,
            "all_passed": passed == len(entries),
        },
    }

    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    out = settings.reports_dir / "validation_report.json"
    out.write_text(json.dumps(report, indent=2))
    log.info("validation report written to %s (all_passed=%s)", out, report["summary"]["all_passed"])
    return report


if __name__ == "__main__":
    rep = run_validation()
    print(json.dumps(rep["summary"], indent=2))
