"""Stateful streaming STFT analysis and overlap-add synthesis.

Unlike the batch core, streaming does **not** centre-pad: frame ``k`` starts
at absolute sample ``k * hop_length`` and is emitted as soon as its ``n_fft``
input samples have arrived. ``flush()`` zero-pads the tail so that every
received sample is covered by at least one frame.

``StreamOla`` performs the inverse direction: it accumulates irfft frames
into numerator/denominator buffers and emits normalised samples as soon as
they are *final* — a sample is final once every frame that can overlap it
has been pushed, i.e. its index is below ``frames_received * hop_length``.
Only the configured kept region is emitted and denominator-checked, matching
the batch core's centre-pad/crop convention.
"""

from __future__ import annotations

import numpy as np

from .errors import ShapeMismatchError, StreamStateError
from .stft_core import (
    StftParams,
    check_ola_denominator,
    validate_params,
    window_buffer,
)


class StreamStft:
    """Incremental STFT analysis with absolute sample bookkeeping."""

    def __init__(self, params: StftParams):
        self._params = validate_params(params)
        self._window = window_buffer(params)
        self._buf = np.zeros(0, dtype=np.float64)
        self._samples_received = 0
        self._frames_emitted = 0
        self._flushed = False

    @property
    def samples_received(self) -> int:
        return self._samples_received

    @property
    def frames_emitted(self) -> int:
        return self._frames_emitted

    @property
    def next_frame_start(self) -> int:
        """Absolute sample index where the next frame will start."""
        return self._frames_emitted * self._params.hop_length

    @property
    def flushed(self) -> bool:
        return self._flushed

    def push(self, chunk: np.ndarray | list[float]) -> np.ndarray:
        """Accept samples; return newly completed frames, shape (m, n_bins)."""
        if self._flushed:
            raise StreamStateError("push() called after flush()")
        arr = np.asarray(chunk, dtype=np.float64)
        if arr.ndim != 1:
            raise ShapeMismatchError(
                f"stream chunk must be 1-D, got shape {arr.shape}"
            )
        self._buf = np.concatenate([self._buf, arr])
        self._samples_received += arr.size
        return self._drain(final=False)

    def flush(self) -> np.ndarray:
        """Emit zero-padded tail frames covering every received sample."""
        if self._flushed:
            raise StreamStateError("flush() called twice")
        self._flushed = True
        return self._drain(final=True)

    def _drain(self, final: bool) -> np.ndarray:
        p = self._params
        frames: list[np.ndarray] = []
        while self._buf.size >= p.n_fft:
            frames.append(np.fft.rfft(self._buf[: p.n_fft] * self._window))
            self._buf = self._buf[p.hop_length :]
            self._frames_emitted += 1
        if final and self._samples_received > 0:
            # Emit frames while the frame start still covers a real sample.
            while self.next_frame_start <= self._samples_received - 1:
                if self._buf.size < p.n_fft:
                    self._buf = np.concatenate(
                        [self._buf, np.zeros(p.n_fft - self._buf.size)]
                    )
                frames.append(np.fft.rfft(self._buf[: p.n_fft] * self._window))
                self._buf = self._buf[p.hop_length :]
                self._frames_emitted += 1
        if not frames:
            return np.zeros((0, p.n_bins), dtype=np.complex128)
        return np.stack(frames)


class StreamOla:
    """Incremental ISTFT via overlap-add with running normalisation.

    Follows the same "kept region" convention as the batch core: only
    positions in ``[kept_offset, kept_offset + kept_length)`` are emitted and
    denominator-checked. ``kept_offset`` defaults to ``params.pad`` so that
    frames produced from a centre-padded signal reconstruct the original
    signal directly; the zero-padding margins (where a symmetric window's
    denominator is legitimately ~0) are never emitted. ``kept_length`` may be
    supplied at construction or at ``flush()`` time; without it, ``flush()``
    emits and validates everything to the end of the buffer — which raises
    ``NotReconstructibleError`` if the tail was not padded, because those
    samples genuinely cannot be reconstructed.
    """

    def __init__(
        self,
        params: StftParams,
        kept_offset: int | None = None,
        kept_length: int | None = None,
    ):
        self._params = validate_params(params)
        self._window = window_buffer(params)
        self._wsq = self._window * self._window
        self._kept_offset = (
            self._params.pad if kept_offset is None else kept_offset
        )
        if self._kept_offset < 0:
            raise StreamStateError("kept_offset must be >= 0")
        self._kept_length = kept_length
        self._num = np.zeros(0, dtype=np.float64)
        self._den = np.zeros(0, dtype=np.float64)
        self._frames_received = 0
        self._emitted = self._kept_offset
        self._flushed = False

    @property
    def frames_received(self) -> int:
        return self._frames_received

    @property
    def samples_emitted(self) -> int:
        return self._emitted

    def _kept_end(self) -> int | None:
        if self._kept_length is None:
            return None
        return self._kept_offset + self._kept_length

    def push_frames(self, frames: np.ndarray) -> np.ndarray:
        """Add one-sided spectrogram frames; return newly final samples."""
        if self._flushed:
            raise StreamStateError("push_frames() called after flush()")
        S = np.asarray(frames, dtype=np.complex128)
        if S.ndim == 1:
            S = S[None, :]
        if S.ndim != 2 or S.shape[1] != self._params.n_bins:
            raise ShapeMismatchError(
                f"frames must have shape (m, {self._params.n_bins}), "
                f"got {S.shape}"
            )
        p = self._params
        n_new = S.shape[0]
        out_end = (self._frames_received + n_new - 1) * p.hop_length + p.n_fft
        if out_end > self._num.size:
            grow = out_end - self._num.size
            self._num = np.concatenate([self._num, np.zeros(grow)])
            self._den = np.concatenate([self._den, np.zeros(grow)])
        time_frames = np.fft.irfft(S, n=p.n_fft, axis=1) * self._window
        for i in range(n_new):
            s = (self._frames_received + i) * p.hop_length
            self._num[s : s + p.n_fft] += time_frames[i]
            self._den[s : s + p.n_fft] += self._wsq
        self._frames_received += n_new
        # Samples below the next frame start can no longer be overlapped.
        limit = self._frames_received * p.hop_length
        kept_end = self._kept_end()
        if kept_end is not None:
            limit = min(limit, kept_end)
        return self._emit_upto(limit)

    def flush(self, kept_length: int | None = None) -> np.ndarray:
        """Emit all remaining kept samples (after denominator check)."""
        if self._flushed:
            raise StreamStateError("flush() called twice")
        self._flushed = True
        if kept_length is not None:
            self._kept_length = kept_length
        kept_end = self._kept_end()
        return self._emit_upto(
            self._num.size if kept_end is None else kept_end
        )

    def _emit_upto(self, limit: int) -> np.ndarray:
        limit = min(limit, self._num.size)
        if limit <= self._emitted:
            return np.zeros(0, dtype=np.float64)
        seg_den = self._den[self._emitted : limit]
        check_ola_denominator(seg_den, self._params)
        out = self._num[self._emitted : limit] / seg_den
        self._emitted = limit
        return out
