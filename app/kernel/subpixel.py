"""Sub-pixel peak refinement by separable parabolic interpolation.

The parabola is fitted to log(surface) when all three samples on an axis
are positive (Gaussian peak model — measurably less bias than a linear
fit on the sinc-like whitened peak), and to the raw samples otherwise.

Failure conditions (contract item 2) are returned explicitly, never
silently patched over:

* ``PEAK_ON_BORDER`` — the integer peak touches the border of the valid
  range, so the 3-point neighbourhood is incomplete.
* ``NON_CONCAVE_NEIGHBORHOOD`` — the peak is not concave along an axis
  (denominator >= 0); a parabola has no maximum there.
* ``FIT_OUT_OF_RANGE`` — the fitted offset exceeds +/-1 px; the fit is
  untrustworthy and the offset is discarded.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

PEAK_ON_BORDER = "PEAK_ON_BORDER"
NON_CONCAVE = "NON_CONCAVE_NEIGHBORHOOD"
FIT_OUT_OF_RANGE = "FIT_OUT_OF_RANGE"


def parabolic_extremum(ym: float, y0: float, yp: float) -> tuple[float, bool]:
    """Vertex offset of the parabola through (-1, ym), (0, y0), (1, yp).

    Returns ``(offset, ok)``; ``ok`` is False when the samples are not
    concave (no interior maximum).
    """
    denom = ym - 2.0 * y0 + yp
    if denom >= 0.0:
        return 0.0, False
    return 0.5 * (ym - yp) / denom, True


def _axis_offset(ym: float, y0: float, yp: float) -> tuple[float, bool]:
    if min(ym, y0, yp) > 0.0:
        return parabolic_extremum(
            float(np.log(ym)), float(np.log(y0)), float(np.log(yp))
        )
    return parabolic_extremum(ym, y0, yp)


@dataclass
class SubpixelResult:
    offset: tuple[float, float]
    ok: bool
    failures: list[str] = field(default_factory=list)


def estimate_subpixel(
    surface: np.ndarray, iy: int, ix: int
) -> SubpixelResult:
    """Fit a separable parabola around surface[iy, ix]."""
    h, w = surface.shape
    if iy == 0 or ix == 0 or iy == h - 1 or ix == w - 1:
        return SubpixelResult(offset=(0.0, 0.0), ok=False, failures=[PEAK_ON_BORDER])

    off_y, ok_y = _axis_offset(
        float(surface[iy - 1, ix]),
        float(surface[iy, ix]),
        float(surface[iy + 1, ix]),
    )
    off_x, ok_x = _axis_offset(
        float(surface[iy, ix - 1]),
        float(surface[iy, ix]),
        float(surface[iy, ix + 1]),
    )

    failures: list[str] = []
    if not (ok_y and ok_x):
        failures.append(NON_CONCAVE)
        off_y, off_x = 0.0, 0.0
    elif abs(off_y) > 1.0 or abs(off_x) > 1.0:
        failures.append(FIT_OUT_OF_RANGE)
        off_y, off_x = 0.0, 0.0

    return SubpixelResult(offset=(off_y, off_x), ok=not failures, failures=failures)
