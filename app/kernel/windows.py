"""Apodization windows. The window choice is explicit configuration,
never silently applied, because it changes the effective support of the
correlation (contract item 1)."""

from __future__ import annotations

import numpy as np
from scipy.signal.windows import hann


def make_window(shape: tuple[int, int], kind: str) -> np.ndarray:
    """Return a 2-D window of ``shape``; ``kind`` is "hann" or "none"."""
    if kind == "none":
        return np.ones(shape, dtype=np.float64)
    if kind == "hann":
        # Separable symmetric Hann: suppresses the edge discontinuity that
        # the DFT's circular assumption would otherwise turn into leakage.
        wy = hann(shape[0], sym=True)
        wx = hann(shape[1], sym=True)
        return np.outer(wy, wx).astype(np.float64)
    raise ValueError(f"unknown window kind: {kind!r} (expected 'hann' or 'none')")
