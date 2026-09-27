"""In-process session management.

Each session owns an isolated Rete engine (own working memory, agenda and
network) plus its session id; all sessions share the configured SQLite
evidence file, with every row scoped by session id.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from rete import Engine, __version__
from rete.config import EngineConfig
from rete.store import EvidenceStore


class Session:
    def __init__(self, session_id: str, store: EvidenceStore,
                 config: EngineConfig):
        self.id = session_id
        self.engine = Engine(config=config, store=store)
        self.engine.session_id = session_id
        self.store = store
        # The engine itself is single-threaded; serialize per-session ops.
        self.lock = threading.Lock()


class SessionManager:
    def __init__(self, store: EvidenceStore, config: EngineConfig | None = None,
                 max_sessions: int = 100):
        self._store = store
        self._config = config or EngineConfig()
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._max = max_sessions

    def create(self, session_id: str) -> Session:
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                return existing
            if len(self._sessions) >= self._max:
                raise RuntimeError("session limit reached")
            session = Session(session_id, self._store, self._config)
            self._sessions[session_id] = session
            return session

    def get(self, session_id: str) -> Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def list(self) -> list[dict]:
        with self._lock:
            return [{
                "session_id": s.id,
                "rules": s.engine.rules(),
                "fact_count": len(s.engine.facts),
                "agenda_size": len(s.engine.agenda),
            } for s in self._sessions.values()]

    def record_rule(self, session: Session, rule_dict: dict) -> None:
        self._store.record_rule(
            session.id, rule_dict["name"], rule_dict,
            datetime.now(timezone.utc).isoformat())

    @property
    def engine_version(self) -> str:
        return __version__
