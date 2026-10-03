"""In-memory session registry.

Sessions are local, ephemeral, and identified by a random hex id.  There is
no persistence and no external identity — this service is a local fixture
backend, so a process-local dict guarded by a lock is sufficient.
"""
from __future__ import annotations

import threading
import uuid

import numpy as np

from app.config import Settings
from app.convolution import SwapStrategy
from app.errors import SessionLimitReachedError, SessionNotFoundError
from app.stream import StreamSession


class SessionRegistry:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._sessions: dict[str, StreamSession] = {}
        self._lock = threading.Lock()

    def create(
        self,
        *,
        sample_rate: int,
        block_size: int,
        ir: np.ndarray,
        swap_strategy: SwapStrategy,
        crossfade_blocks: int,
        max_state_bytes: int | None,
    ) -> StreamSession:
        with self._lock:
            if len(self._sessions) >= self._settings.max_sessions:
                raise SessionLimitReachedError(
                    f"session limit {self._settings.max_sessions} reached",
                    detail={"max_sessions": self._settings.max_sessions},
                )
            session = StreamSession(
                session_id=uuid.uuid4().hex[:16],
                sample_rate=sample_rate,
                block_size=block_size,
                ir=ir,
                swap_strategy=swap_strategy,
                crossfade_blocks=crossfade_blocks,
                max_state_bytes=(
                    max_state_bytes
                    if max_state_bytes is not None
                    else self._settings.max_state_bytes
                ),
            )
            self._sessions[session.session_id] = session
            return session

    def get(self, session_id: str) -> StreamSession:
        try:
            return self._sessions[session_id]
        except KeyError:
            raise SessionNotFoundError(
                f"unknown session {session_id!r}; the decision cannot be "
                "evaluated without live session state",
                detail={"session_id": session_id},
            ) from None

    def delete(self, session_id: str) -> None:
        with self._lock:
            if self._sessions.pop(session_id, None) is None:
                raise SessionNotFoundError(
                    f"unknown session {session_id!r}",
                    detail={"session_id": session_id},
                )

    def __len__(self) -> int:
        return len(self._sessions)
