"""Frame-wise streaming analysis / synthesis state.

The streaming path reuses the *exact* same windowing and OLA rules as the
batch path in :mod:`stft_backend.algorithms`:

* the analyzer prepends the ``nperseg // 2`` boundary extension internally,
  so frame indices and sample positions match the batch transform;
* the synthesizer accumulates the same windowed IFFT frames and the same
  ``|w|^2`` denominator, and only normalizes/trims on ``finish``, where the
  non-zero-denominator check is enforced.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .algorithms import (
    HERMITIAN_TOL,
    _normalize_and_trim,
    _recover_full_spectrum,
)
from .errors import ErrorCode, StftError
from .numeric import nola_diagnostics

DIRECTION_ANALYZE = "analyze"
DIRECTION_SYNTHESIZE = "synthesize"
_VALID_DIRECTIONS = (DIRECTION_ANALYZE, DIRECTION_SYNTHESIZE)


def _frame_location(frame_index: int, hop: int, nperseg: int) -> dict:
    start = frame_index * hop - nperseg // 2
    # With boundary extension the window peak lands on sample m*hop for
    # both even and odd nperseg.
    return {
        "frame_index": frame_index,
        "start_sample": start,
        "center_sample": frame_index * hop,
    }


class StreamAnalyzer:
    """Emits STFT frames as input chunks arrive.

    Internal buffer coordinates are the *extended* coordinates of the batch
    transform: index 0 of the buffer is the first boundary zero.
    """

    def __init__(
        self,
        *,
        nperseg: int,
        hop: int,
        nfft: int,
        window: np.ndarray,
        onesided: bool,
    ) -> None:
        nola_diagnostics(window, hop, nfft)
        self.nperseg = nperseg
        self.hop = hop
        self.nfft = nfft
        self.window = window
        self.onesided = onesided
        self._pad = nperseg // 2
        self._buffer = np.zeros(self._pad, dtype=np.float64)
        self._emitted = 0
        self._finished = False
        self.input_length = 0

    @property
    def emitted_frames(self) -> int:
        return self._emitted

    def _make_frame(self, frame_index: int, extended: np.ndarray) -> np.ndarray:
        segment = extended * self.window
        if self.nfft > self.nperseg:
            segment = np.pad(segment, (0, self.nfft - self.nperseg))
        if self.onesided:
            return np.fft.rfft(segment)
        return np.fft.fft(segment)

    def _drain(self) -> list[dict]:
        frames: list[dict] = []
        while self._emitted * self.hop + self.nperseg <= len(self._buffer):
            start = self._emitted * self.hop
            extended = self._buffer[start : start + self.nperseg]
            bins = self._make_frame(self._emitted, extended)
            frames.append(
                {
                    **_frame_location(self._emitted, self.hop, self.nperseg),
                    "bins": _pack_complex(bins),
                }
            )
            self._emitted += 1
        return frames

    def push(self, samples: np.ndarray) -> list[dict]:
        if self._finished:
            raise StftError(
                ErrorCode.FRAME_SEQUENCE_ERROR,
                "Cannot push samples after the stream was finished.",
                stage="stream_analyze",
            )
        arr = np.asarray(samples, dtype=np.float64)
        if arr.ndim != 1 or not np.all(np.isfinite(arr)):
            raise StftError(
                ErrorCode.NON_FINITE_SIGNAL,
                "Chunk must be a one-dimensional finite real array.",
                stage="stream_analyze",
            )
        self.input_length += arr.size
        self._buffer = np.concatenate([self._buffer, arr])
        return self._drain()

    def finish(self) -> list[dict]:
        """Append boundary/trailing extension and emit all remaining frames."""

        if self._finished:
            return []
        # Right boundary extension, then trailing alignment to the hop grid
        # (same construction as algorithms._framed_signal).
        extended = np.pad(self._buffer, (0, self._pad))
        trail = (-((len(extended) - self.nperseg) % self.hop)) % self.hop
        if trail:
            extended = np.pad(extended, (0, trail))
        self._buffer = extended
        self._finished = True
        return self._drain()


def _unpack_complex(bins: Any, expected: int, *, frame_index: int) -> np.ndarray:
    """Decode ``[[real, imag], ...]`` wire pairs into a complex vector."""

    if not isinstance(bins, (list, tuple)):
        raise StftError(
            ErrorCode.SPECTRUM_SHAPE_MISMATCH,
            f"Frame {frame_index}: bins must be a list of [real, imag] pairs.",
            stage="stream_synthesize",
            details={"frame_index": frame_index},
        )
    values: list[complex] = []
    for i, pair in enumerate(bins):
        if (
            not isinstance(pair, (list, tuple))
            or len(pair) != 2
            or not all(isinstance(v, (int, float)) for v in pair)
        ):
            raise StftError(
                ErrorCode.SPECTRUM_SHAPE_MISMATCH,
                f"Frame {frame_index}: bin {i} must be a [real, imag] pair "
                "of numbers.",
                stage="stream_synthesize",
                details={"frame_index": frame_index, "bin_index": i},
            )
        values.append(complex(float(pair[0]), float(pair[1])))
    if len(values) != expected:
        raise StftError(
            ErrorCode.SPECTRUM_SHAPE_MISMATCH,
            f"Frame {frame_index}: expected {expected} bins, got "
            f"{len(values)}.",
            stage="stream_synthesize",
            details={
                "frame_index": frame_index,
                "expected_bins": expected,
                "got_bins": len(values),
            },
        )
    arr = np.asarray(values, dtype=np.complex128)
    if not np.all(np.isfinite(arr.real)) or not np.all(np.isfinite(arr.imag)):
        raise StftError(
            ErrorCode.SPECTRUM_SHAPE_MISMATCH,
            f"Frame {frame_index}: bins contain non-finite values.",
            stage="stream_synthesize",
            details={"frame_index": frame_index},
        )
    return arr


def _pack_complex(bins: np.ndarray) -> list[list[float]]:
    return [[float(z.real), float(z.imag)] for z in bins]


class StreamSynthesizer:
    """Accumulates incoming frames via weighted overlap-add."""

    def __init__(
        self,
        *,
        nperseg: int,
        hop: int,
        nfft: int,
        window: np.ndarray,
        onesided: bool,
    ) -> None:
        nola_diagnostics(window, hop, nfft)
        self.nperseg = nperseg
        self.hop = hop
        self.nfft = nfft
        self.window = window
        self.onesided = onesided
        self._expected_bins = nfft // 2 + 1 if onesided else nfft
        self._next_frame = 0
        self._finished = False
        self._numerator = np.zeros(0, dtype=np.float64)
        self._denominator = np.zeros(0, dtype=np.float64)

    @property
    def received_frames(self) -> int:
        return self._next_frame

    def _grow(self, end: int) -> None:
        if end > self._numerator.size:
            self._numerator = np.pad(self._numerator, (0, end - self._numerator.size))
            self._denominator = np.pad(
                self._denominator, (0, end - self._denominator.size)
            )

    def push_frame(self, frame_index: int, bins: Any) -> dict:
        if self._finished:
            raise StftError(
                ErrorCode.FRAME_SEQUENCE_ERROR,
                "Cannot push frames after the stream was finished.",
                stage="stream_synthesize",
            )
        if frame_index != self._next_frame:
            raise StftError(
                ErrorCode.FRAME_SEQUENCE_ERROR,
                f"Expected frame_index={self._next_frame}, got {frame_index}. "
                "Frames must arrive in strict order starting at 0; gaps and "
                "duplicates are rejected.",
                stage="stream_synthesize",
                details={"expected": self._next_frame, "got": frame_index},
            )
        column = _unpack_complex(
            bins, self._expected_bins, frame_index=frame_index
        )
        if self.onesided:
            full = _recover_full_spectrum(
                column[:, None], self.nfft, HERMITIAN_TOL
            )[:, 0]
        else:
            full = column
        time_frame = np.fft.ifft(full, n=self.nfft)[: self.nperseg]
        start = frame_index * self.hop
        end = start + self.nperseg
        self._grow(end)
        self._numerator[start:end] += np.real(time_frame * self.window)
        self._denominator[start:end] += np.abs(self.window) ** 2
        self._next_frame += 1
        return _frame_location(frame_index, self.hop, self.nperseg)

    def finish(self, signal_length: int | None = None) -> np.ndarray:
        if self._next_frame == 0:
            raise StftError(
                ErrorCode.FRAME_SEQUENCE_ERROR,
                "Cannot finish: no frames were received.",
                stage="stream_synthesize",
            )
        result = _normalize_and_trim(
            self._numerator,
            self._denominator,
            n_frames=self._next_frame,
            nperseg=self.nperseg,
            hop=self.hop,
            signal_length=signal_length,
        )
        self._finished = True
        return result


@dataclass
class Session:
    session_id: str
    direction: str
    nperseg: int
    hop: int
    nfft: int
    onesided: bool
    window: np.ndarray = field(repr=False)
    analyzer: StreamAnalyzer | None = None
    synthesizer: StreamSynthesizer | None = None
    closed: bool = False


class SessionStore:
    """In-memory session registry. Local backend only; no persistence."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(
        self,
        *,
        direction: str,
        nperseg: int,
        hop: int,
        nfft: int,
        window: np.ndarray,
        onesided: bool,
    ) -> Session:
        if direction not in _VALID_DIRECTIONS:
            raise StftError(
                ErrorCode.INVALID_PARAMETER,
                f"direction must be one of {_VALID_DIRECTIONS}, got {direction!r}.",
                stage="session",
            )
        session_id = uuid.uuid4().hex
        if direction == DIRECTION_ANALYZE:
            worker: StreamAnalyzer | StreamSynthesizer = StreamAnalyzer(
                nperseg=nperseg,
                hop=hop,
                nfft=nfft,
                window=window,
                onesided=onesided,
            )
            session = Session(
                session_id=session_id,
                direction=direction,
                nperseg=nperseg,
                hop=hop,
                nfft=nfft,
                onesided=onesided,
                window=window,
                analyzer=worker,
            )
        else:
            synth = StreamSynthesizer(
                nperseg=nperseg,
                hop=hop,
                nfft=nfft,
                window=window,
                onesided=onesided,
            )
            session = Session(
                session_id=session_id,
                direction=direction,
                nperseg=nperseg,
                hop=hop,
                nfft=nfft,
                onesided=onesided,
                window=window,
                synthesizer=synth,
            )
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str, *, expected_direction: str | None = None) -> Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise StftError(
                ErrorCode.SESSION_NOT_FOUND,
                f"Unknown or expired session id {session_id!r}.",
                stage="session",
                details={"session_id": session_id},
            )
        if expected_direction is not None and session.direction != expected_direction:
            raise StftError(
                ErrorCode.SESSION_DIRECTION_CONFLICT,
                f"Session {session_id!r} was created for "
                f"{session.direction!r}, not {expected_direction!r}.",
                stage="session",
                details={
                    "session_id": session_id,
                    "created_direction": session.direction,
                    "requested_direction": expected_direction,
                },
            )
        if session.closed:
            raise StftError(
                ErrorCode.FRAME_SEQUENCE_ERROR,
                f"Session {session_id!r} is already closed.",
                stage="session",
                details={"session_id": session_id},
            )
        return session

    def close(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            session.closed = True

    def __len__(self) -> int:
        return len(self._sessions)
