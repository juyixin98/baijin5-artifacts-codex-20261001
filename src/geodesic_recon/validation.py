"""Post-hoc property checks for reconstruction results.

Checks are deliberately implemented *without* the production kernel where
possible: the fixed-point check uses SciPy's ``maximum_filter`` as an
independent dilation, so a bug in ``kernel.dilate_once`` cannot silently
validate itself.

Properties verified:
* within_mask   -- result <= mask everywhere
* above_marker  -- result >= marker everywhere (extensivity)
* fixed_point   -- min(dilate(result), mask) == result
* idempotent    -- R(R(marker)) == R(marker)
* monotone      -- marker_a <= marker_b  =>  R(marker_a) <= R(marker_b)

Note on the boundary rule: ``maximum_filter(mode="nearest")`` pads with edge
values, which only duplicates values already inside the window; a maximum is
insensitive to duplicates, so it matches the kernel's edge-ignore rule exactly.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from .kernel import reconstruct


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    violations: int
    detail: str = ""


@dataclass(frozen=True)
class ValidationReport:
    checks: tuple[CheckResult, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "violations": c.violations,
                    "detail": c.detail,
                }
                for c in self.checks
            ],
        }


def _scipy_dilate(values: np.ndarray, connectivity: int) -> np.ndarray:
    footprint = ndimage.generate_binary_structure(2, 1 if connectivity == 4 else 2)
    return ndimage.maximum_filter(values, footprint=footprint, mode="nearest")


def verify_result(
    marker: np.ndarray,
    mask: np.ndarray,
    result: np.ndarray,
    connectivity: int = 4,
) -> ValidationReport:
    """Verify that ``result`` is a valid reconstruction of marker under mask."""
    marker = np.asarray(marker, dtype=np.float64)
    mask = np.asarray(mask, dtype=np.float64)
    result = np.asarray(result, dtype=np.float64)
    checks: list[CheckResult] = []

    if result.shape != mask.shape or marker.shape != mask.shape:
        return ValidationReport(
            checks=(
                CheckResult(
                    name="shape_consistent",
                    passed=False,
                    violations=1,
                    detail=(
                        f"marker{marker.shape} mask{mask.shape} result{result.shape} differ"
                    ),
                ),
            )
        )

    below = result < marker
    checks.append(
        CheckResult("above_marker", not below.any(), int(below.sum()), "result < marker")
    )

    above = result > mask
    checks.append(
        CheckResult("within_mask", not above.any(), int(above.sum()), "result > mask")
    )

    regrown = np.minimum(_scipy_dilate(result, connectivity), mask)
    unstable = regrown != result
    checks.append(
        CheckResult(
            "fixed_point",
            not unstable.any(),
            int(unstable.sum()),
            "min(dilate(result), mask) != result",
        )
    )
    return ValidationReport(checks=tuple(checks))


def check_idempotent(
    marker: np.ndarray,
    mask: np.ndarray,
    *,
    algorithm: str = "queue",
    connectivity: int = 4,
) -> CheckResult:
    once, _ = reconstruct(marker, mask, algorithm=algorithm, connectivity=connectivity)
    twice, _ = reconstruct(once, mask, algorithm=algorithm, connectivity=connectivity)
    diff = int(np.count_nonzero(once != twice))
    return CheckResult("idempotent", diff == 0, diff, "R(R(f)) != R(f)")


def check_monotone(
    marker_low: np.ndarray,
    marker_high: np.ndarray,
    mask: np.ndarray,
    *,
    algorithm: str = "queue",
    connectivity: int = 4,
) -> CheckResult:
    """Requires marker_low <= marker_high <= mask (caller-side precondition)."""
    low, _ = reconstruct(marker_low, mask, algorithm=algorithm, connectivity=connectivity)
    high, _ = reconstruct(marker_high, mask, algorithm=algorithm, connectivity=connectivity)
    bad = low > high
    return CheckResult("monotone", not bad.any(), int(bad.sum()), "R(f1) > R(f2) for f1 <= f2")
