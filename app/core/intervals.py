"""Interval-set algebra for confidence sets.

The acceptance set ``{tau : p(tau) >= alpha}`` for a sign-flip statistic is a
finite union of closed intervals and need *not* be connected (the statistic is
piecewise linear in tau and the two-sided absolute value creates crossings).
This module represents it as an ordered list of disjoint intervals and
deliberately provides no operation that collapses gaps.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class Interval:
    lower: float
    upper: float
    lower_inclusive: bool = True
    upper_inclusive: bool = True

    @property
    def is_singleton(self) -> bool:
        return self.lower == self.upper

    def to_dict(self) -> dict:
        # +/-infinity serializes as JSON null (with explicit *_finite flags),
        # because strict JSON has no infinity literal.
        return {
            "lower": self.lower if math.isfinite(self.lower) else None,
            "upper": self.upper if math.isfinite(self.upper) else None,
            "lower_finite": math.isfinite(self.lower),
            "upper_finite": math.isfinite(self.upper),
            "lower_inclusive": self.lower_inclusive,
            "upper_inclusive": self.upper_inclusive,
        }


@dataclass(frozen=True)
class IntervalSet:
    intervals: tuple[Interval, ...]
    grid_resolution: float | None = None
    note: str = ""

    @property
    def n_intervals(self) -> int:
        return len(self.intervals)

    @property
    def is_connected(self) -> bool:
        """True only when the set has at most one component."""
        return len(self.intervals) <= 1

    def contains(self, value: float, tolerance: float = 0.0) -> bool:
        for iv in self.intervals:
            if iv.lower - tolerance <= value <= iv.upper + tolerance:
                return True
        return False

    def to_dict(self) -> dict:
        return {
            "intervals": [iv.to_dict() for iv in self.intervals],
            "n_intervals": self.n_intervals,
            "is_connected": self.is_connected,
            "grid_resolution": self.grid_resolution,
            "note": self.note,
        }


def intervals_from_mask(grid: np.ndarray, accepted: np.ndarray) -> IntervalSet:
    """Group consecutive accepted grid points into disjoint closed intervals.

    A rejected grid point strictly between accepted points splits two
    intervals; the gap is never bridged. Isolated accepted points become
    singleton intervals ``[x, x]``.

    Endpoints carry the grid discretization uncertainty: the true boundary of
    a non-singleton component lies within half a grid step of the reported
    endpoint, which is communicated via ``grid_resolution``.
    """
    if grid.shape != accepted.shape:
        raise ValueError("grid and accepted must have the same shape")
    if accepted.size == 0 or not accepted.any():
        return IntervalSet(
            intervals=(),
            grid_resolution=float(grid[1] - grid[0]) if grid.size >= 2 else None,
            note="empty acceptance set at the evaluated grid",
        )

    resolution = float(grid[1] - grid[0]) if grid.size >= 2 else 0.0
    intervals: list[Interval] = []
    idx = 0
    n = accepted.size
    while idx < n:
        if not accepted[idx]:
            idx += 1
            continue
        start = idx
        while idx + 1 < n and accepted[idx + 1]:
            idx += 1
        end = idx
        lo = float(grid[start])
        hi = float(grid[end])
        if lo == hi:
            intervals.append(Interval(lo, hi))
        else:
            intervals.append(
                Interval(
                    lo,
                    hi,
                    lower_inclusive=True,
                    upper_inclusive=True,
                )
            )
        idx += 1

    return IntervalSet(
        intervals=tuple(intervals),
        grid_resolution=resolution if resolution > 0 else None,
        note=(
            "components evaluated on a finite grid; endpoints are accurate to "
            "within one grid step"
            if resolution > 0
            else ""
        ),
    )


def sets_match_within_tolerance(
    a: Sequence[Interval], b: Sequence[Interval], tolerance: float
) -> bool:
    """Two interval sets agree if every endpoint matches within ``tolerance``.

    Used by the independent-evidence cross check: the kernel grid and the
    oracle grid differ, so exact endpoint equality is not expected.
    """
    if len(a) != len(b):
        return False
    for iv_a, iv_b in zip(a, b):
        if not math.isclose(
            iv_a.lower, iv_b.lower, abs_tol=tolerance, rel_tol=0.0
        ) or not math.isclose(
            iv_a.upper, iv_b.upper, abs_tol=tolerance, rel_tol=0.0
        ):
            return False
    return True
