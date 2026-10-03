"""Validation: compare tiled output against the direct full-image reference.

The reference is computed by :func:`tileconv.kernel.direct_reference`
(scipy.ndimage with native boundary modes) — never by the tiled engine under
test. Reports carry the tolerance, the decision basis, content digests and
component versions so a verdict can be audited after the fact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .contract import BoundaryMode, KernelSpec
from .kernel import direct_reference
from .logging_utils import RunLogger, versions_snapshot

REFERENCE_DESCRIPTION = (
    "scipy.ndimage.convolve/convolve1d with native boundary modes "
    "(odd-centered kernel, origin=0); no tiling, no manual padding"
)


@dataclass
class ComparisonReport:
    passed: bool
    max_abs_diff: float
    mean_abs_diff: float
    mismatch_count: int
    shape: tuple[int, int]
    atol: float
    rtol: float
    basis: str
    reference: str = REFERENCE_DESCRIPTION
    versions: dict[str, str] = field(default_factory=versions_snapshot)
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "max_abs_diff": self.max_abs_diff,
            "mean_abs_diff": self.mean_abs_diff,
            "mismatch_count": self.mismatch_count,
            "shape": list(self.shape),
            "tolerance": {"atol": self.atol, "rtol": self.rtol},
            "basis": self.basis,
            "reference": self.reference,
            "versions": self.versions,
            "context": self.context,
        }


def compare_arrays(
    actual: np.ndarray,
    expected: np.ndarray,
    atol: float = 1e-9,
    rtol: float = 1e-9,
    context: Optional[dict[str, Any]] = None,
) -> ComparisonReport:
    """Elementwise comparison with basis ``|a-e| <= atol + rtol*|e|``."""
    actual = np.asarray(actual, dtype=np.float64)
    expected = np.asarray(expected, dtype=np.float64)
    if actual.shape != expected.shape:
        return ComparisonReport(
            passed=False,
            max_abs_diff=float("inf"),
            mean_abs_diff=float("inf"),
            mismatch_count=-1,
            shape=tuple(actual.shape),
            atol=atol,
            rtol=rtol,
            basis=f"shape mismatch: actual {actual.shape} vs expected {expected.shape}",
            context=context or {},
        )
    diff = np.abs(actual - expected)
    tol = atol + rtol * np.abs(expected)
    mismatches = int(np.count_nonzero(diff > tol))
    return ComparisonReport(
        passed=mismatches == 0,
        max_abs_diff=float(diff.max()) if diff.size else 0.0,
        mean_abs_diff=float(diff.mean()) if diff.size else 0.0,
        mismatch_count=mismatches,
        shape=tuple(actual.shape),
        atol=atol,
        rtol=rtol,
        basis=(
            f"pass iff |actual-expected| <= atol({atol}) + rtol({rtol})*|expected| "
            f"for every pixel; mismatches={mismatches}"
        ),
        context=context or {},
    )


def validate_output(
    output: np.ndarray,
    image: np.ndarray,
    kernel: KernelSpec,
    boundary: BoundaryMode,
    cval: float = 0.0,
    atol: float = 1e-9,
    rtol: float = 1e-9,
    context: Optional[dict[str, Any]] = None,
    run_logger: Optional[RunLogger] = None,
    report_path: Optional[Path] = None,
) -> ComparisonReport:
    """Compute the direct reference for ``image`` and compare with ``output``."""
    expected = direct_reference(image, kernel, boundary, cval)
    report = compare_arrays(output, expected, atol=atol, rtol=rtol, context=context)
    if run_logger is not None:
        run_logger.log(
            "validation",
            passed=report.passed,
            max_abs_diff=report.max_abs_diff,
            mismatch_count=report.mismatch_count,
            tolerance={"atol": atol, "rtol": rtol},
            basis=report.basis,
            reference=report.reference,
            versions=report.versions,
            context=context or {},
        )
    if report_path is not None:
        Path(report_path).write_text(json.dumps(report.to_dict(), indent=1))
    return report
