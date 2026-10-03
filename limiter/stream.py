"""Streaming lookahead limiter state machine.

Pipeline per input frame k (see config.py for the fixed contract):

1. peak(k) = max over channels of |frame|
2. r(k)    = min(1, threshold / peak(k))
3. R       = min of r over the lookahead window r(k-L .. k)
             (monotonic deque, O(1) per frame)
4. g       = one-pole attack/release smoother step toward R
5. out     = delay_line_read() * g   (frame that entered L frames ago)

Latency: output frame j becomes available exactly when input frame j+L
has been consumed, i.e. the algorithmic latency is L frames. process()
emits only frames that are ready, so after N input frames (no flush)
exactly max(0, N-L) frames have been emitted. flush() pushes L silent
frames through the detector so the final L real frames leave the delay
line — total emitted == total input, no tail samples dropped.

Because the lookahead window keeps a peak's r(k) in R until the peak
itself has left the delay line, the smoother never releases early into
a peak, and the analytic ceiling of LimiterConfig.promised_ceiling holds
for every frame, including the flushed tail.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from .config import LimiterConfig
from .contract import ContractError, validate_block
from .envelope import required_gain, smooth_step


@dataclass
class BlockResult:
    """Output frames produced by one process()/flush() call."""

    pcm: np.ndarray  # (frames, channels) emitted output
    gain: np.ndarray  # (frames,) gain applied to each emitted frame
    start_index: int  # global output-frame index of pcm[0]


@dataclass
class OfflineResult:
    """Latency-compensated result of a whole signal (process + flush)."""

    output: np.ndarray  # (N, channels), aligned to the input timeline
    gain: np.ndarray  # (N,) gain trajectory, gain[j] applied to input frame j
    latency_samples: int
    input_peak: float
    output_peak: float
    promised_ceiling: float


class StreamingLimiter:
    def __init__(self, config: LimiterConfig | None = None):
        self.config = config or LimiterConfig()
        L = self.config.lookahead_samples
        self._delay = np.zeros((L, self.config.channels), dtype=np.float64)
        self._pos = 0  # ring position of the oldest buffered frame
        self._g = 1.0  # smoother state
        self._window: deque[tuple[int, float]] = deque()  # monotonic min of r
        self._in = 0  # input frames consumed
        self._out = 0  # output frames emitted
        self._flushed = False

    @property
    def latency_samples(self) -> int:
        return self.config.lookahead_samples

    @property
    def frames_emitted(self) -> int:
        return self._out

    def _step(self, frame: np.ndarray) -> tuple[np.ndarray, float]:
        """Advance one input frame; return (output_frame, gain)."""
        k = self._in
        L = self.config.lookahead_samples
        peak = float(np.max(np.abs(frame)))
        r = required_gain(peak, self.config.threshold)

        # Lookahead window: min of r over input indices [k-L, k].
        w = self._window
        while w and w[-1][1] >= r:
            w.pop()
        w.append((k, r))
        oldest = k - L
        while w and w[0][0] < oldest:
            w.popleft()
        window_min = w[0][1]

        self._g = smooth_step(
            self._g, window_min, self.config.attack_coeff, self.config.release_coeff
        )

        delayed = self._delay[self._pos].copy()
        self._delay[self._pos] = frame
        self._pos = (self._pos + 1) % L
        self._in += 1
        return delayed * self._g, self._g

    def _run(self, pcm: np.ndarray) -> BlockResult:
        L = self.config.lookahead_samples
        out_frames: list[np.ndarray] = []
        out_gains: list[float] = []
        start_index = self._out
        for i in range(len(pcm)):
            out, g = self._step(pcm[i])
            # The first L frames ever stepped are the zero-fill of the delay
            # line; they carry no input signal and are suppressed so that
            # output frame index j always corresponds to input frame j.
            # During flush this same condition emits exactly the L buffered
            # tail frames (or N frames total when N < L — no tail is lost).
            if self._in > L:
                out_frames.append(out)
                out_gains.append(g)
                self._out += 1
        pcm_out = (
            np.stack(out_frames).astype(np.float64)
            if out_frames
            else np.zeros((0, self.config.channels), dtype=np.float64)
        )
        return BlockResult(pcm=pcm_out, gain=np.asarray(out_gains), start_index=start_index)

    def process(self, pcm) -> BlockResult:
        """Consume a block; emit every output frame that is ready."""
        if self._flushed:
            raise ContractError("stream_flushed", "cannot process after flush()")
        block = validate_block(pcm, self.config.channels)
        return self._run(block)

    def flush(self) -> BlockResult:
        """Drain the delay line; emits exactly the final L frames."""
        if self._flushed:
            raise ContractError("stream_flushed", "flush() called twice")
        self._flushed = True
        L = self.config.lookahead_samples
        zeros = np.zeros((L, self.config.channels), dtype=np.float64)
        return self._run(zeros)


def process_offline(pcm, config: LimiterConfig | None = None) -> OfflineResult:
    """Process a whole signal and return the latency-compensated result.

    ``output[j] = input[j] * gain[j]``; the arrays are aligned to the input
    timeline (the L-frame algorithmic latency is compensated by flush).
    """
    cfg = config or LimiterConfig()
    block = validate_block(pcm, cfg.channels)
    lim = StreamingLimiter(cfg)
    head = lim.process(block)
    tail = lim.flush()
    output = np.vstack([head.pcm, tail.pcm])
    gain = np.concatenate([head.gain, tail.gain])
    input_peak = float(np.max(np.abs(block))) if len(block) else 0.0
    output_peak = float(np.max(np.abs(output))) if len(output) else 0.0
    return OfflineResult(
        output=output,
        gain=gain,
        latency_samples=cfg.lookahead_samples,
        input_peak=input_peak,
        output_peak=output_peak,
        promised_ceiling=cfg.promised_ceiling(input_peak),
    )
