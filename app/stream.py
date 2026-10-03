"""Stream state: per-session analysis/synthesis filter state across frames.

A stream session owns one :class:`AnalysisFilter` and one
:class:`SynthesisFilter`. Because the analysis filter's history is exactly
the initial state the synthesis filter needs
(:func:`app.lpc.filters.corresponding_synthesis_state`), pushing frames
through a session in ``roundtrip`` mode reconstructs the concatenated
input signal exactly, not just frame-locally.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass

from app.lpc.filters import AnalysisFilter, SynthesisFilter


@dataclass
class StreamSession:
    stream_id: str
    order: int
    window: str
    analysis_filter: AnalysisFilter
    synthesis_filter: SynthesisFilter
    frames_processed: int = 0

    def snapshot(self) -> dict:
        return {
            "stream_id": self.stream_id,
            "order": self.order,
            "window": self.window,
            "frames_processed": self.frames_processed,
            "analysis_state": self.analysis_filter.state.tolist(),
            "synthesis_state": self.synthesis_filter.state.tolist(),
        }


class StreamManager:
    """Thread-safe registry of stream sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, StreamSession] = {}
        self._lock = threading.Lock()

    def create(self, order: int, window: str) -> StreamSession:
        session = StreamSession(
            stream_id=uuid.uuid4().hex,
            order=order,
            window=window,
            analysis_filter=AnalysisFilter(order),
            synthesis_filter=SynthesisFilter(order),
        )
        with self._lock:
            self._sessions[session.stream_id] = session
        return session

    def get(self, stream_id: str) -> StreamSession:
        with self._lock:
            try:
                return self._sessions[stream_id]
            except KeyError:
                raise KeyError(f"unknown stream id: {stream_id}") from None

    def delete(self, stream_id: str) -> None:
        with self._lock:
            if self._sessions.pop(stream_id, None) is None:
                raise KeyError(f"unknown stream id: {stream_id}")

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)
