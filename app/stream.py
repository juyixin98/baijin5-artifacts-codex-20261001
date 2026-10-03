"""Stream state: frame-by-frame LPC analysis and reconstruction.

The processor carries the analysis filter's final state of frame k as
the initial state of frame k+1; the reconstructor does the same for the
synthesis filter. Because the two state sequences are built from the
same sample history, they correspond — streaming a signal frame by
frame yields bit-identical residuals and reconstructions to one-shot
processing (asserted by the test-suite).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.lpc.autocorr import apply_window, autocorrelation
from app.lpc.filters import analysis_filter, synthesis_filter
from app.lpc.levinson import LevinsonResult, levinson_durbin


@dataclass(frozen=True)
class FrameAnalysis:
    """Per-frame analysis output (immutable)."""

    frame_index: int
    offset: int
    levinson: LevinsonResult
    residual: np.ndarray


class LPCStreamAnalyzer:
    """Stateful frame-by-frame analyzer.

    The window is applied only for coefficient estimation; the residual
    is computed on the *unwindowed* frame so analysis/synthesis stay
    exact inverses.
    """

    def __init__(self, frame_size: int, order: int, window: str) -> None:
        if order < 1 or order >= frame_size:
            raise ValueError(
                f"need 1 <= order < frame_size; got order={order}, "
                f"frame_size={frame_size}"
            )
        self.frame_size = frame_size
        self.order = order
        self.window = window
        self._analysis_state = np.zeros(order)
        self._frames_seen = 0
        self._samples_seen = 0

    @property
    def frames_seen(self) -> int:
        return self._frames_seen

    def process_frame(self, frame: np.ndarray) -> FrameAnalysis:
        frame = np.asarray(frame, dtype=np.float64)
        if frame.size != self.frame_size:
            raise ValueError(
                f"frame must have exactly {self.frame_size} samples, "
                f"got {frame.size}"
            )
        windowed = apply_window(frame, self.window)
        r = autocorrelation(windowed, self.order)
        result = levinson_durbin(r, self.order)
        residual, self._analysis_state = analysis_filter(
            frame, result.lpc, zi=self._analysis_state
        )
        analysis = FrameAnalysis(
            frame_index=self._frames_seen,
            offset=self._samples_seen,
            levinson=result,
            residual=residual,
        )
        self._frames_seen += 1
        self._samples_seen += frame.size
        return analysis


class LPCStreamReconstructor:
    """Stateful frame-by-frame reconstructor (synthesis side)."""

    def __init__(self, order: int) -> None:
        if order < 1:
            raise ValueError("order must be >= 1")
        self.order = order
        self._synthesis_state = np.zeros(order)
        self._frames_seen = 0

    @property
    def frames_seen(self) -> int:
        return self._frames_seen

    def reconstruct_frame(self, residual: np.ndarray, lpc: np.ndarray) -> np.ndarray:
        lpc = np.asarray(lpc, dtype=np.float64)
        if lpc.size - 1 != self.order:
            raise ValueError(
                f"lpc implies order {lpc.size - 1}, reconstructor has {self.order}"
            )
        y, self._synthesis_state = synthesis_filter(
            residual, lpc, zi=self._synthesis_state
        )
        self._frames_seen += 1
        return y
