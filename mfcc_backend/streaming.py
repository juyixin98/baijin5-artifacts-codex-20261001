"""Streaming MFCC with causal context handling.

Rules (mirrors the batch spec exactly):

- Samples are pre-emphasised on arrival (causal filter: only the last
  sample of the previous chunk is carried across the boundary, matching
  the batch x[-1]=0 rule at the stream start), then buffered; a frame is
  emitted as soon as ``frame_length`` samples are available, and the
  buffer advances by ``hop_length``.
- A trailing partial frame is dropped at ``finish()`` — the same rule the
  batch framer applies, so stream-concatenation equals batch output.
- MFCC rows are final the moment their frame is complete (framing is
  causal; no future sample ever touches an emitted MFCC row).
- A delta row ``t`` is emitted only once frame ``t + N`` exists
  (N = delta_width): it never assumes future samples. ``finish()``
  releases the last N rows using the batch edge-replication rule.
- delta2 lags delta by another N rows for the same reason.
- ``finish()`` with zero complete frames raises InsufficientSignalError
  instead of returning an empty "success".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import MFCCConfig
from .dsp import (
    build_mel_filterbank,
    compute_delta,
    frame_signal,
    log_mel_spectrum,
    mfcc_from_log_mel,
    power_spectrum,
)
from .errors import InsufficientSignalError, SessionStateError
from .pipeline import validate_samples


@dataclass
class StreamEmission:
    """Rows that became final during one accept_chunk()/finish() call."""

    mfcc: np.ndarray
    delta: np.ndarray
    delta2: np.ndarray

    def counts(self) -> dict:
        return {"mfcc": len(self.mfcc), "delta": len(self.delta), "delta2": len(self.delta2)}


@dataclass
class StreamingMFCC:
    config: MFCCConfig
    _buffer: np.ndarray = field(init=False, repr=False)
    _mfcc_rows: list = field(init=False, repr=False, default_factory=list)
    _delta_rows: list = field(init=False, repr=False, default_factory=list)
    _delta2_rows: list = field(init=False, repr=False, default_factory=list)
    _emitted: dict = field(init=False, repr=False)
    _finished: bool = field(init=False, default=False)
    chunks_accepted: int = field(init=False, default=0)

    def __post_init__(self):
        self.config.validate()
        self._bank = build_mel_filterbank(self.config)
        self._buffer = np.empty(0, dtype=np.float64)
        self._prev_raw = 0.0  # x[-1] = 0 boundary, same as the batch rule
        self._emitted = {"mfcc": 0, "delta": 0, "delta2": 0}

    # -- public API ---------------------------------------------------------
    def accept_chunk(self, samples) -> StreamEmission:
        if self._finished:
            raise SessionStateError("stream is finished; no further chunks accepted")
        arr = validate_samples(samples)  # empty chunk is an explicit client error
        self.chunks_accepted += 1

        # Causal pre-emphasis across the chunk boundary: y[0] of this chunk
        # uses the last raw sample of the previous chunk (or 0 at stream start).
        coef = self.config.preemphasis_coef
        pe = arr - coef * np.concatenate(([self._prev_raw], arr[:-1]))
        self._prev_raw = float(arr[-1])
        self._buffer = np.concatenate([self._buffer, pe])

        fl, hop = self.config.frame_length, self.config.hop_length
        if self._buffer.size >= fl:
            n_new = 1 + (self._buffer.size - fl) // hop
            frames = frame_signal(self._buffer[: (n_new - 1) * hop + fl], fl, hop)
            self._mfcc_rows.extend(self._mfcc_of_frames(frames))
            self._buffer = self._buffer[n_new * hop :]
        return self._drain(final=False)

    def finish(self) -> StreamEmission:
        if self._finished:
            raise SessionStateError("stream already finished")
        self._finished = True
        # Remaining buffered samples form an incomplete frame -> dropped,
        # exactly like the batch framer drops the trailing partial frame.
        self._buffer = np.empty(0, dtype=np.float64)
        if not self._mfcc_rows:
            raise InsufficientSignalError(
                f"stream produced 0 complete frames "
                f"(frame_length={self.config.frame_length})",
                detail={"frame_length": self.config.frame_length},
            )
        return self._drain(final=True)

    # -- internals ------------------------------------------------------------
    def _mfcc_of_frames(self, frames: np.ndarray) -> np.ndarray:
        """Same stage functions as the batch pipeline, applied per block."""
        power = power_spectrum(frames, self.config.nfft)
        mel_energies = power @ self._bank.T
        log_mel = log_mel_spectrum(mel_energies, self.config.log_floor)
        return mfcc_from_log_mel(log_mel, self.config.n_mfcc)

    def _drain(self, final: bool) -> StreamEmission:
        n = self.config.delta_width
        mfcc = np.asarray(self._mfcc_rows, dtype=np.float64).reshape(-1, self.config.n_mfcc)

        # Delta rows are final only when N future frames exist (or at finish,
        # where the batch edge-replication rule takes over).
        delta_final = len(mfcc) if final else max(0, len(mfcc) - n)
        if len(mfcc):
            delta_all = compute_delta(mfcc, n)
            self._extend(self._delta_rows, delta_all[:delta_final])

        delta_mat = np.asarray(self._delta_rows, dtype=np.float64).reshape(-1, self.config.n_mfcc)
        delta2_final = len(delta_mat) if final else max(0, len(delta_mat) - n)
        if len(delta_mat):
            delta2_all = compute_delta(delta_mat, n)
            self._extend(self._delta2_rows, delta2_all[:delta2_final])

        emission = StreamEmission(
            mfcc=mfcc[self._emitted["mfcc"] :],
            delta=np.asarray(self._delta_rows, dtype=np.float64).reshape(-1, self.config.n_mfcc)[
                self._emitted["delta"] :
            ],
            delta2=np.asarray(self._delta2_rows, dtype=np.float64).reshape(-1, self.config.n_mfcc)[
                self._emitted["delta2"] :
            ],
        )
        self._emitted["mfcc"] = len(mfcc)
        self._emitted["delta"] = len(self._delta_rows)
        self._emitted["delta2"] = len(self._delta2_rows)
        return emission

    @staticmethod
    def _extend(rows: list, block: np.ndarray):
        if len(block) > len(rows):
            rows.extend(block[len(rows) :])

    # -- introspection --------------------------------------------------------
    def totals(self) -> dict:
        return {
            "frames": len(self._mfcc_rows),
            "emitted": dict(self._emitted),
            "buffered_samples": int(self._buffer.size),
            "finished": self._finished,
            "chunks_accepted": self.chunks_accepted,
        }
