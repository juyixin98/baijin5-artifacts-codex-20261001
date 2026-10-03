"""Stateful chunked (streaming) WSOLA.

WsolaStream applies the SAME fixed rules as wsola.wsola_stretch, frame by
frame, as input chunks arrive. Given the same total input it produces
bit-identical output and identical per-segment offsets (verified by
tests/test_stream.py). This works because:

* A frame k can be decided without future knowledge once the input covers
  nominal_k + search_radius + window_length: then the right clamp of the
  search range cannot bind, and the left clamp depends only on nominal_k.
* The frame cap K depends on the total length, so mid-stream it is
  evaluated against the bytes received so far (a monotone lower bound of
  the final K); frames beyond the final K are never placed early.
* Frames whose search range touches the not-yet-received tail are deferred
  to finalize(), where the total length is known and the same clamp the
  offline core uses is applied.

Samples are emitted only when final: after frame k is placed, all output
positions < k * Hs have their complete window sum.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.signal import windows

from .config import DEFAULT_CONFIG, WsolaConfig
from .wsola import (
    InputTooShortError,
    SegmentDecision,
    StretchResult,
    _search_delta,
    search_range_for,
    target_length_for,
)


class StreamFinalizedError(RuntimeError):
    """push() called after finalize()."""


class WsolaStream:
    def __init__(self, rate: float, cfg: WsolaConfig = DEFAULT_CONFIG) -> None:
        if not math.isfinite(rate) or rate <= 0.0:
            raise ValueError(f"rate must be a positive finite number, got {rate!r}")
        self._cfg = cfg
        self._rate = rate
        self._analysis_hop = rate * cfg.synthesis_hop
        self._window = windows.hann(cfg.window_length, sym=False)
        self._overlap_weight = self._window[cfg.synthesis_hop :]
        self._input = np.zeros(0)
        self._y = np.zeros(0)
        self._envelope = np.zeros(0)
        self._segments: list[SegmentDecision] = []
        self._next_frame = 0
        self._emitted = 0
        self._max_drift = 0.0
        self._finalized = False

    @property
    def segments(self) -> list[SegmentDecision]:
        return list(self._segments)

    def push(self, samples: np.ndarray) -> np.ndarray:
        """Append an input chunk; return newly finalized output samples."""
        if self._finalized:
            raise StreamFinalizedError("stream already finalized")
        chunk = np.asarray(samples, dtype=np.float64)
        if chunk.ndim != 1:
            raise ValueError("expected a mono 1-D chunk")
        self._input = np.concatenate([self._input, chunk])
        self._place_available(final=False)
        return self._emit_finalized()

    def finalize(self) -> StretchResult:
        """Flush with end-of-input clamps and the end-length compensation."""
        if self._finalized:
            raise StreamFinalizedError("stream already finalized")
        self._finalized = True
        n = self._input.shape[0]
        if n < self._cfg.window_length:
            raise InputTooShortError(n, self._cfg.window_length)
        self._place_available(final=True)

        placed = self._next_frame
        valid_length = (placed - 1) * self._cfg.synthesis_hop + self._cfg.window_length
        target = target_length_for(n, self._rate)
        already = self._emitted
        # Tail pinning guarantees valid_length >= target: trim or exact.
        assert valid_length >= target
        end_rule = "exact" if valid_length == target else "trim"
        compensation = target - valid_length  # <= 0
        tail = self._normalized(already, target)
        self._emitted = already + tail.shape[0]

        return StretchResult(
            samples=tail,  # final emitted chunk; full output = all chunks joined
            target_length=target,
            segments=list(self._segments),
            frames_planned=max(
                1,
                math.ceil((target - self._cfg.window_length) / self._cfg.synthesis_hop)
                + 1,
            ),
            frames_placed=placed,
            end_rule=end_rule,
            end_compensation_samples=compensation,
            max_position_drift=self._max_drift,
        )

    # -- internals ---------------------------------------------------------

    def _ensure_capacity(self, length: int) -> None:
        if self._y.shape[0] < length:
            grow = length - self._y.shape[0]
            self._y = np.concatenate([self._y, np.zeros(grow)])
            self._envelope = np.concatenate([self._envelope, np.zeros(grow)])

    def _place_available(self, final: bool) -> None:
        cfg = self._cfg
        Hs, L, D = cfg.synthesis_hop, cfg.window_length, cfg.search_radius
        n = self._input.shape[0]
        while True:
            k = self._next_frame
            ideal = k * self._analysis_hop
            nominal = int(round(ideal))
            if final:
                target = target_length_for(n, self._rate)
                frames_planned = max(1, math.ceil((target - L) / Hs) + 1)
                if k >= frames_planned:
                    return
            else:
                # Frame cap with the bytes seen so far (monotone lower bound).
                target_now = target_length_for(n, self._rate)
                frames_now = max(1, math.ceil((target_now - L) / Hs) + 1)
                if k >= frames_now:
                    return
                if nominal + D + L > n:
                    return  # right clamp could still move; wait for more input
            self._max_drift = max(self._max_drift, abs(nominal - ideal))
            out_pos = k * Hs
            self._ensure_capacity(out_pos + L)
            if k == 0:
                delta, correlation, degenerate, pinned = 0, None, False, False
            else:
                lo, hi, pinned = search_range_for(nominal, n, cfg)
                delta, correlation, degenerate = _search_delta(
                    self._y[out_pos : out_pos + Hs],
                    self._input,
                    nominal,
                    lo,
                    hi,
                    cfg,
                    self._overlap_weight,
                )
            analysis_pos = nominal + delta
            self._y[out_pos : out_pos + L] += (
                self._window * self._input[analysis_pos : analysis_pos + L]
            )
            self._envelope[out_pos : out_pos + L] += self._window
            self._segments.append(
                SegmentDecision(
                    index=k,
                    output_position=out_pos,
                    ideal_position=ideal,
                    nominal_position=nominal,
                    delta=delta,
                    analysis_position=analysis_pos,
                    correlation=correlation,
                    degenerate=degenerate,
                    pinned=pinned,
                )
            )
            self._next_frame += 1

    def _normalized(self, start: int, stop: int) -> np.ndarray:
        out = np.zeros(stop - start)
        np.divide(
            self._y[start:stop],
            self._envelope[start:stop],
            out=out,
            where=self._envelope[start:stop] > self._cfg.envelope_epsilon,
        )
        return out

    def _emit_finalized(self) -> np.ndarray:
        # After placing frame k, positions < k*Hs have complete window sums.
        final_up_to = self._next_frame * self._cfg.synthesis_hop
        if final_up_to <= self._emitted:
            return np.zeros(0)
        chunk = self._normalized(self._emitted, final_up_to)
        self._emitted = final_up_to
        return chunk
