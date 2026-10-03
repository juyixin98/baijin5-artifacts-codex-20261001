"""Stateful analysis and synthesis filters with corresponding states.

Analysis (FIR):   e[n] = x[n] + sum_{k=1..p} a[k] * x[n-k]
Synthesis (IIR):  y[n] = e[n] - sum_{k=1..p} a[k] * y[n-k]

State correspondence
--------------------
The analysis filter's state is the last p *input* samples it has seen.
The synthesis filter's state is the last p *output* samples it has
produced. For the synthesis filter to exactly invert the analysis
filter, its initial state must equal the last p samples of the original
signal preceding the residual — i.e. the analysis filter's own history
at the moment the residual frame starts
(:func:`corresponding_synthesis_state`). Both filters start at zero
state, so a stream that alternates analyze/synthesize frame-by-frame
keeps the correspondence automatically.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import lfilter


class AnalysisFilter:
    """FIR analysis filter carrying the last ``order`` input samples."""

    def __init__(self, order: int):
        if order < 1:
            raise ValueError("order must be >= 1")
        self.order = order
        self.history = np.zeros(order)

    def process(self, frame: np.ndarray, coefficients: np.ndarray) -> np.ndarray:
        frame = np.asarray(frame, dtype=float)
        a = np.asarray(coefficients, dtype=float)
        if a.size != self.order + 1:
            raise ValueError(
                f"expected {self.order + 1} coefficients, got {a.size}"
            )
        ext = np.concatenate([self.history, frame])
        residual = lfilter(a, [1.0], ext)[self.order :]
        self.history = ext[-self.order :].copy()
        return residual

    @property
    def state(self) -> np.ndarray:
        return self.history.copy()


class SynthesisFilter:
    """IIR synthesis filter carrying the last ``order`` output samples."""

    def __init__(self, order: int, initial_state: np.ndarray | None = None):
        if order < 1:
            raise ValueError("order must be >= 1")
        self.order = order
        if initial_state is None:
            self.history = np.zeros(order)
        else:
            initial_state = np.asarray(initial_state, dtype=float)
            if initial_state.size != order:
                raise ValueError(
                    f"initial state must have {order} samples, "
                    f"got {initial_state.size}"
                )
            self.history = initial_state.copy()

    def process(self, residual: np.ndarray, coefficients: np.ndarray) -> np.ndarray:
        residual = np.asarray(residual, dtype=float)
        a = np.asarray(coefficients, dtype=float)
        if a.size != self.order + 1:
            raise ValueError(
                f"expected {self.order + 1} coefficients, got {a.size}"
            )
        hist = self.history.tolist()
        out = np.empty(residual.size)
        for n in range(residual.size):
            acc = float(residual[n])
            for k in range(1, self.order + 1):
                acc -= a[k] * hist[-k]
            out[n] = acc
            hist.append(acc)
        self.history = np.asarray(hist[-self.order :])
        return out

    @property
    def state(self) -> np.ndarray:
        return self.history.copy()


def corresponding_synthesis_state(analysis_filter: AnalysisFilter) -> np.ndarray:
    """Synthesis initial state that exactly inverts this analysis filter.

    The returned vector is the analysis filter's history: the last p
    samples of the original signal. Initialising the synthesis filter
    with anything else breaks exact reconstruction of the next frame.
    """
    return analysis_filter.state
