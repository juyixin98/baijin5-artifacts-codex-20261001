"""Fixed alignment constraints: step pattern and Sakoe-Chiba path window.

Contract guarantees enforced here:

* The step pattern contains no pure horizontal ``(0, k)`` or vertical
  ``(k, 0)`` moves, so a path can never run along a single axis without
  consuming samples from both sequences. Local slope is bounded to
  ``[1/2, 2]`` samples-of-A per sample-of-B, which rules out the
  pathological compressions an unconstrained ``(1, 0) / (0, 1)`` pattern
  allows.
* The Sakoe-Chiba window ``|i - j| <= radius`` bounds how far the path may
  drift from the diagonal; combined with the step pattern this makes
  endpoint reachability decidable up front (``|n - m| <= radius``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StepPattern:
    """Allowed transitions ``(di, dj)`` between consecutive path cells.

    Every transition consumes the arrival cell's local distance exactly once;
    the accumulated cost of a path is the sum of local distances over its
    cells. The default pattern ``{(1,1), (2,1), (1,2)}`` is the smallest
    slope-constrained pattern that still permits 2:1 and 1:2 local warping.
    """

    steps: tuple[tuple[int, int], ...] = ((1, 1), (2, 1), (1, 2))

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError("step pattern must contain at least one step")
        for di, dj in self.steps:
            if di < 0 or dj < 0 or (di == 0 and dj == 0):
                raise ValueError(f"non-monotone step ({di}, {dj}) is not allowed")
            if di == 0 or dj == 0:
                raise ValueError(
                    f"pure-axis step ({di}, {dj}) would allow unbounded "
                    "horizontal/vertical runs and is not allowed"
                )

    @property
    def max_step(self) -> int:
        return max(max(di, dj) for di, dj in self.steps)


DEFAULT_STEP_PATTERN = StepPattern()


@dataclass(frozen=True)
class SakoeChibaWindow:
    """Sakoe-Chiba band: cell ``(i, j)`` is legal iff ``|i - j| <= radius``."""

    radius: int

    def __post_init__(self) -> None:
        if self.radius < 0:
            raise ValueError(f"window radius must be >= 0, got {self.radius}")

    def contains(self, i: int, j: int) -> bool:
        return abs(i - j) <= self.radius

    def j_range(self, i: int, n_cols: int) -> tuple[int, int]:
        """Inclusive ``[j_lo, j_hi]`` column range inside the band for row ``i``."""
        return max(0, i - self.radius), min(n_cols - 1, i + self.radius)

    def endpoint_reachable(self, n: int, m: int) -> bool:
        """Necessary condition for any legal path to reach ``(n-1, m-1)``."""
        return abs(n - m) <= self.radius
