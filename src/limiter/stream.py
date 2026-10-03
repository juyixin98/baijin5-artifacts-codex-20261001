"""Streaming limiter state machine.

Latency model (delay-line contract)
-----------------------------------
The output stream is the limited input delayed by exactly L =
``lookahead_samples`` samples:

- ``process(block)`` consumes B samples and emits B samples; emitted
  position i carries ``x[i-L] * g[i-L]`` (zeros while i < L). Emission of
  position i only requires input up to sample i, and the gain for it only
  reads the L-sample lookahead window, so a real-time consumer sees a
  fixed delay of L.
- ``flush()`` emits the final L samples still sitting in the delay line,
  so no tail samples are lost. Total emitted = total consumed + L.

Delay compensation
------------------
``latency_samples`` reports L. Concatenating every emitted block and
dropping the first L samples re-aligns output with input (see
``offline.limit_offline``).

Block-size independence
-----------------------
The gain trajectory depends only on the input signal and config, never on
how the input was chunked: the attack min-filter only reads the L buffered
future samples, and the release state carries across blocks.
"""

from __future__ import annotations

import numpy as np

from .config import LimiterConfig
from .envelope import attack_limited_minimum, release_pass, required_gain
from .peaks import make_detector
from .runlog import RunLog, library_versions, pcm_sha256


class LimiterStream:
    def __init__(
        self,
        config: LimiterConfig,
        num_channels: int = 2,
        run_log: RunLog | None = None,
    ):
        self._cfg = config.validate()
        if num_channels < 1:
            raise ValueError("num_channels must be >= 1")
        self._channels = num_channels
        self._detector = make_detector(self._cfg.true_peak, self._cfg.oversample_factor)
        self._log = run_log or RunLog()
        # Input samples whose gain is not yet computable (lookahead window).
        self._pending = np.zeros((0, num_channels), dtype=np.float64)
        # Limited samples (x * g) ready to leave the delay line.
        self._ready = np.zeros((0, num_channels), dtype=np.float64)
        self._ready_gain = np.zeros(0, dtype=np.float64)
        # Already-consumed tail kept as filter context for true-peak mode.
        self._hist = np.zeros((0, num_channels), dtype=np.float64)
        self._gain_state = 1.0
        self._zeros_left = self._cfg.lookahead_samples  # leading delay zeros
        self._total_in = 0
        self._total_out = 0
        self._block_index = 0
        self._flushed = False
        self._min_gain = 1.0
        self._max_out_peak = 0.0
        self._log.event(
            "run_start",
            versions=library_versions(),
            config=self._cfg.to_dict(),
            num_channels=num_channels,
            latency_samples=self.latency_samples,
        )

    # -- introspection ------------------------------------------------------

    @property
    def latency_samples(self) -> int:
        return self._cfg.lookahead_samples

    @property
    def run_id(self) -> str:
        return self._log.run_id

    @property
    def stats(self) -> dict:
        return {
            "run_id": self.run_id,
            "total_in": self._total_in,
            "total_out": self._total_out,
            "latency_samples": self.latency_samples,
            "min_gain": self._min_gain,
            "max_output_peak": self._max_out_peak,
            "threshold_linear": self._cfg.threshold_linear,
            "sample_peak_ceiling_ok": self._max_out_peak
            <= self._cfg.threshold_linear * (1.0 + 1e-9),
        }

    # -- processing ---------------------------------------------------------

    def process(self, block: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Consume B samples, emit B samples (delayed by ``latency_samples``)."""
        if self._flushed:
            raise RuntimeError("stream already flushed; create a new LimiterStream")
        block = self._check_block(block)
        self._pending = np.vstack([self._pending, block])
        self._total_in += block.shape[0]
        n_gain = self._pending.shape[0] - self._cfg.lookahead_samples
        if n_gain > 0:
            self._compute_gains(n_gain)
        out, gain = self._emit(block.shape[0])
        self._log.event(
            "block",
            block_index=self._block_index,
            n_in=int(block.shape[0]),
            n_emit=int(block.shape[0]),
            input_sha256=pcm_sha256(block),
            block_min_gain=float(gain.min()) if gain.size else 1.0,
            block_max_out_peak=float(np.max(np.abs(out))) if out.size else 0.0,
        )
        self._block_index += 1
        return out, gain

    def flush(self) -> tuple[np.ndarray, np.ndarray]:
        """Emit the L tail samples still in the delay line."""
        if self._flushed:
            raise RuntimeError("flush() may only be called once")
        self._flushed = True
        # All remaining pending samples get their gain now; the attack
        # window beyond the end sees r = 1 (silence), which is exactly the
        # zero-future the delay line assumes.
        if self._pending.shape[0]:
            self._compute_gains(self._pending.shape[0])
        n_emit = self._zeros_left + self._ready.shape[0]
        out, gain = self._emit(n_emit)
        self._log.event(
            "flush",
            n_emit=int(n_emit),
            tail_max_peak=float(np.max(np.abs(out))) if n_emit else 0.0,
        )
        self._log.event("run_end", **self.stats)
        return out, gain

    # -- internals ----------------------------------------------------------

    def _check_block(self, block: np.ndarray) -> np.ndarray:
        block = np.asarray(block, dtype=np.float64)
        if block.ndim == 1:
            if self._channels != 1:
                raise ValueError(
                    f"expected 2-D block with {self._channels} channels, got 1-D"
                )
            block = block[:, None]
        if block.ndim != 2 or block.shape[1] != self._channels:
            raise ValueError(
                f"expected block of shape (n, {self._channels}), got {block.shape}"
            )
        if not np.all(np.isfinite(block)):
            raise ValueError("block contains non-finite samples")
        return block

    def _compute_gains(self, n: int) -> None:
        """Compute gains for the first n pending samples; move to ready."""
        cfg = self._cfg
        context = np.vstack([self._hist, self._pending])
        peaks = self._detector.peaks(context)[self._hist.shape[0] :]
        r = required_gain(peaks, cfg.threshold_linear)
        # Emitted sample k needs r up to k + L; pending holds >= n + L.
        g1 = attack_limited_minimum(r, cfg.attack_coeff, cfg.lookahead_samples)
        gain = release_pass(g1[:n], cfg.release_coeff, self._gain_state)
        self._gain_state = float(gain[-1])
        self._min_gain = min(self._min_gain, float(gain.min()))

        consumed = self._pending[:n]
        self._ready = np.vstack([self._ready, consumed * gain[:, None]])
        self._ready_gain = np.concatenate([self._ready_gain, gain])
        self._pending = self._pending[n:]
        guard = self._detector.guard_samples
        if guard:
            self._hist = np.vstack([self._hist, consumed])[-guard:]

    def _emit(self, n: int) -> tuple[np.ndarray, np.ndarray]:
        """Emit n samples from the output stream (leading zeros, then ready)."""
        parts, gains = [], []
        remaining = n
        if self._zeros_left and remaining:
            z = min(remaining, self._zeros_left)
            parts.append(np.zeros((z, self._channels)))
            gains.append(np.ones(z))
            self._zeros_left -= z
            remaining -= z
        if remaining:
            if self._ready.shape[0] < remaining:
                raise RuntimeError(
                    "internal underrun: not enough limited samples to emit"
                )
            parts.append(self._ready[:remaining])
            gains.append(self._ready_gain[:remaining])
            self._ready = self._ready[remaining:]
            self._ready_gain = self._ready_gain[remaining:]
        out = np.vstack(parts) if parts else np.zeros((0, self._channels))
        gain = np.concatenate(gains) if gains else np.zeros(0)
        if out.size:
            self._max_out_peak = max(self._max_out_peak, float(np.max(np.abs(out))))
        self._total_out += n
        return out, gain
