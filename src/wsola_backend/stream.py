"""Incremental WSOLA stream state.

WsolaStream accepts input in chunks and emits finalized output as soon as
enough lookahead is buffered. A frame k is only computed once the full search
window (k*Ha + search_radius + window_len) is available, or at finish(); this
makes chunked processing bit-identical to the batch wsola_stretch result.
"""
from __future__ import annotations

import numpy as np
from scipy.signal.windows import hann

from .wsola import (
    StretchResult,
    WsolaParams,
    compute_frame,
    frame_count,
    make_params,
    normalize_region,
    target_length,
)
from .wsola import FrameDecision  # noqa: F401  (re-exported for callers)


class WsolaStream:
    def __init__(self, sample_rate: int, time_scale: float):
        self.params: WsolaParams = make_params(sample_rate, time_scale)
        self._window = hann(self.params.window_len, sym=False)
        self._chunks: list[np.ndarray] = []
        self._x = np.empty(0, dtype=np.float64)  # materialized input so far
        self._out = np.zeros(0, dtype=np.float64)
        self._wsum = np.zeros(0, dtype=np.float64)
        self._next_frame = 0
        self._emitted = 0
        self.frames: list[FrameDecision] = []
        self._finished = False

    # -- internal helpers ----------------------------------------------------

    def _ensure_capacity(self, n: int) -> None:
        if n <= self._out.shape[0]:
            return
        grow = max(n, max(1, 2 * self._out.shape[0]))
        self._out = np.concatenate([self._out, np.zeros(grow - self._out.shape[0])])
        self._wsum = np.concatenate([self._wsum, np.zeros(grow - self._wsum.shape[0])])

    def _frame_ready(self, k: int, n_available: int) -> bool:
        """Frame k is computable without clamping once its full search range fits."""
        p = self.params
        return k * p.analysis_hop + p.search_radius + p.window_len <= n_available

    def _run_frame(self, k: int) -> None:
        p = self.params
        self._ensure_capacity(k * p.synthesis_hop + p.window_len)
        decision = compute_frame(self._x, self._out, self._wsum, self._window, p, k)
        self.frames.append(decision)

    def _emit_upto(self, stop: int) -> np.ndarray:
        stop = min(stop, self._out.shape[0])
        if stop <= self._emitted:
            return np.empty(0, dtype=np.float64)
        seg = normalize_region(self._out, self._wsum, self._emitted, stop)
        self._emitted = stop
        return seg

    # -- public API ----------------------------------------------------------

    def push(self, chunk: np.ndarray) -> np.ndarray:
        """Buffer a chunk; return newly finalized output samples (may be empty)."""
        if self._finished:
            raise RuntimeError("push after finish")
        chunk = np.ascontiguousarray(chunk, dtype=np.float64)
        self._chunks.append(chunk)
        self._x = np.concatenate([self._x, chunk]) if self._x.size else chunk.copy()
        n = self._x.shape[0]
        while self._frame_ready(self._next_frame, n):
            self._run_frame(self._next_frame)
            self._next_frame += 1
        # After frame k, samples before (k+1)*Hs can no longer be touched.
        return self._emit_upto(self._next_frame * self.params.synthesis_hop)

    def finish(self) -> np.ndarray:
        """Flush: process remaining frames with end clamping, return the tail.

        The concatenation of all push()/finish() returns has exactly
        round(total_input * time_scale) samples.
        """
        if self._finished:
            raise RuntimeError("finish called twice")
        self._finished = True
        n = self._x.shape[0]
        target = target_length(n, self.params.time_scale)
        k_total = frame_count(self.params, target)
        while self._next_frame < k_total:
            self._run_frame(self._next_frame)
            self._next_frame += 1
        return self._emit_upto(target)

    def result(self) -> StretchResult:
        """Assemble the full result. Only valid after finish()."""
        if not self._finished:
            raise RuntimeError("result() before finish()")
        n = self._x.shape[0]
        target = target_length(n, self.params.time_scale)
        output = normalize_region(self._out, self._wsum, 0, min(target, self._out.shape[0]))
        return StretchResult(output=output, target_length=target, frames=self.frames, params=self.params)
