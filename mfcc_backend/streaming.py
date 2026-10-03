"""Streaming MFCC extraction with explicit context management.

Guarantees:

1. **No future leakage.**  Frame t is emitted only after every sample it can
   depend on has been delivered.  Because delta needs ``delta_width`` future
   frames and delta-delta needs ``2 * delta_width``, emission lags framing by
   ``2 * delta_width`` frames.  Nothing beyond the delivered samples is ever
   read — the lag is context, not leakage.
2. **Sufficient context.**  One raw sample is carried across chunk
   boundaries for pre-emphasis; up to ``frame_length - 1`` leftover samples
   are retained so chunk boundaries never split a frame; the last
   ``2 * delta_width`` emitted MFCC rows are kept as *left* context so delta
   rows at emission boundaries use real history frames, never edge-replicated
   stand-ins (except at the true stream start, exactly as batch does).
3. **Batch equivalence.**  For any partition of the same sample stream,
   concatenate(emitted frames) == extract_features(all samples) for mfcc,
   delta and delta-delta, up to floating-point reduction-order differences
   (~1e-13) from running the FFT/DCT on different block shapes.  The frame
   partitioning, pre-emphasis and delta boundary handling are identical.

Frame emission protocol: ``accept_chunk`` emits frames whose full
``2 * delta_width`` future context has arrived; ``finalize`` flushes the
held-back tail using the fixed edge-replication boundary rule — the same
rule batch applies at the end of the signal.  A trailing partial frame is
discarded, exactly as in batch.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import DEFAULT_CONFIG, MFCCConfig
from .delta import compute_delta
from .errors import StreamStateError
from .filterbank import build_mel_filterbank
from .framing import (
    frame_count,
    frame_signal,
    hamming_window,
    preemphasis,
    validate_samples,
)
from .pipeline import apply_lifter, dct_ii_ortho


@dataclass(frozen=True)
class StreamEmit:
    """Frames emitted by one accept_chunk / finalize call."""

    mfcc: np.ndarray        # (k, n_mfcc)
    delta: np.ndarray       # (k, n_mfcc)
    delta_delta: np.ndarray # (k, n_mfcc)
    start_frame: int        # global index of mfcc[0]
    n_frames: int           # k


@dataclass
class _StreamState:
    raw_tail: float = 0.0           # last raw sample of the previous chunk
    leftover: np.ndarray = field(default_factory=lambda: np.empty(0))
    pending: list[np.ndarray] = field(default_factory=list)   # held-back rows
    history: list[np.ndarray] = field(default_factory=list)   # emitted context
    emitted: int = 0
    seen_samples: int = 0
    finalized: bool = False


class StreamingMFCC:
    """Incremental MFCC extractor; see module docstring for guarantees."""

    def __init__(self, config: MFCCConfig = DEFAULT_CONFIG) -> None:
        self.config = config.validate()
        self._filterbank = build_mel_filterbank(config)
        self._window = hamming_window(config.frame_length)
        self._state = _StreamState()

    # -- public API ----------------------------------------------------------

    @property
    def frames_emitted(self) -> int:
        return self._state.emitted

    @property
    def samples_seen(self) -> int:
        return self._state.seen_samples

    def accept_chunk(self, samples: np.ndarray) -> StreamEmit:
        """Consume the next chunk; emit frames whose context is complete."""
        st = self._require_open()
        x = validate_samples(samples)
        emphasized = preemphasis(x, self.config.preemphasis, initial=st.raw_tail)
        st.raw_tail = float(x[-1])
        st.seen_samples += int(x.size)

        buf = np.concatenate([st.leftover, emphasized])
        n_new = frame_count(buf.size, self.config.frame_length, self.config.hop_length)
        if n_new:
            frames = frame_signal(buf, self.config.frame_length, self.config.hop_length)
            # Keep everything from the *next* frame's start offset onward.
            # Cutting at the last consumed frame's end would discard the
            # hop/frame overlap that future frames still need.
            st.leftover = buf[n_new * self.config.hop_length :]
            st.pending.extend(self._frames_to_mfcc_rows(frames))
        else:
            st.leftover = buf
        return self._emit_ready()

    def finalize(self) -> StreamEmit:
        """Flush held-back frames with edge-replication deltas; close stream."""
        st = self._require_open()
        st.finalized = True
        rows = st.pending
        st.pending = []
        return self._emit(rows, right_context=[])

    # -- internals -----------------------------------------------------------

    @property
    def _hold(self) -> int:
        """Frames held back so delta *and* delta-delta see real futures."""
        return 2 * self.config.delta_width

    def _require_open(self) -> _StreamState:
        st = self._state
        if st.finalized:
            raise StreamStateError(
                "stream is finalized; no further chunks or finalize calls are allowed"
            )
        return st

    def _frames_to_mfcc_rows(self, frames: np.ndarray) -> list[np.ndarray]:
        windowed = frames * self._window
        spectrum = np.abs(np.fft.rfft(windowed, n=self.config.n_fft, axis=1)) ** 2
        power = spectrum / self.config.n_fft
        mel_energy = power @ self._filterbank.T
        log_mel = np.log(np.maximum(mel_energy, self.config.log_floor))
        mfcc = apply_lifter(
            dct_ii_ortho(log_mel, self.config.n_mfcc), self.config.lifter
        )
        return [row for row in mfcc]

    def _emit(self, rows: list[np.ndarray], right_context: list[np.ndarray]) -> StreamEmit:
        """Emit ``rows`` computing deltas over history + rows + right_context.

        The history slice supplies real left-context frames; the right
        context supplies real future frames.  Edge replication therefore only
        ever applies at the genuine stream boundaries, matching batch.
        """
        st = self._state
        n = self.config.n_mfcc
        if not rows:
            empty = np.empty((0, n))
            return StreamEmit(empty, empty, empty, start_frame=st.emitted, n_frames=0)
        context = np.vstack(st.history + rows + right_context)
        base = len(st.history)
        delta_full = compute_delta(context, self.config.delta_width)
        delta2_full = compute_delta(delta_full, self.config.delta_width)
        block = np.vstack(rows)
        out = StreamEmit(
            mfcc=block,
            delta=delta_full[base : base + len(rows)],
            delta_delta=delta2_full[base : base + len(rows)],
            start_frame=st.emitted,
            n_frames=len(rows),
        )
        st.history = (st.history + rows)[-self._hold :]
        st.emitted += len(rows)
        return out

    def _emit_ready(self) -> StreamEmit:
        st = self._state
        n_ready = max(0, len(st.pending) - self._hold)
        if n_ready == 0:
            return self._emit([], right_context=[])
        ready = st.pending[:n_ready]
        tail = st.pending[n_ready:]
        st.pending = tail
        return self._emit(ready, right_context=tail)
