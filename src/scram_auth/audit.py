"""Structured audit logging.

Each request carries a correlation id (``request_id``) that links every event
emitted by one authentication attempt.  Events are JSON objects, one per
line, containing:

* ``ts``                 ISO-8601 UTC timestamp;
* ``request_id``         correlation id shared by the whole exchange;
* ``session_id``         server-side session id, when one exists;
* ``event`` / ``step``   machine-readable lifecycle step;
* ``component``/``version``/``location``  where the event was produced;
* ``outcome``            ``success`` | ``failure`` | ``info`` | ``uncertain``;
* ``failure``            present only on failure, ``{category, message}``;
* ``uncertain``          list of human-readable inconclusive observations;
* ``detail``             non-secret structural context.

Plaintext passwords, proofs, StoredKey/ServerKey and signature bytes are
never logged — only lengths and boolean comparisons.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

COMPONENT = "scram-auth-local"
VERSION = "1.0.0"


def _utc_now() -> str:
    # millisecond precision, explicit Z suffix
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time() * 1000) % 1000:03d}Z"


@dataclass
class AuditEvent:
    request_id: str
    event: str
    step: str
    outcome: str = "info"
    session_id: str | None = None
    location: str = ""
    failure: dict | None = None
    uncertain: list[str] | None = None
    detail: dict | None = None

    def to_json_line(self) -> str:
        record: dict[str, Any] = {
            "ts": _utc_now(),
            "component": COMPONENT,
            "version": VERSION,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "event": self.event,
            "step": self.step,
            "location": self.location,
            "outcome": self.outcome,
        }
        if self.failure is not None:
            record["failure"] = self.failure
        if self.uncertain:
            record["uncertain"] = self.uncertain
        if self.detail:
            record["detail"] = self.detail
        return json.dumps(record, ensure_ascii=False, sort_keys=True)


class AuditLogger:
    """Append-only JSONL audit sink; safe for concurrent use."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # Buffered writer with immediate flush+fsync per event: audit volume for
        # a local test service is tiny; durability of the failure record wins.
        self._fh = self._path.open("a", encoding="utf-8")

    def emit(self, event: AuditEvent) -> None:
        line = event.to_json_line()
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
            os.fsync(self._fh.fileno())

    def info(self, request_id: str, event: str, step: str, *, location: str, **kw: Any) -> None:
        self.emit(AuditEvent(request_id, event, step, "info", location=location, **kw))

    def success(self, request_id: str, event: str, step: str, *, location: str, **kw: Any) -> None:
        self.emit(AuditEvent(request_id, event, step, "success", location=location, **kw))

    def failure(self, request_id: str, event: str, step: str, *, failure: dict, location: str, **kw: Any) -> None:
        self.emit(AuditEvent(request_id, event, step, "failure", failure=failure, location=location, **kw))

    def uncertain(self, request_id: str, event: str, step: str, *, notes: list[str], location: str, **kw: Any) -> None:
        self.emit(AuditEvent(request_id, event, step, "uncertain", uncertain=notes, location=location, **kw))

    def close(self) -> None:
        with self._lock:
            self._fh.close()


class InMemoryAuditLogger(AuditLogger):
    """Audit sink used by tests: keeps parsed events in addition to writing."""

    def __init__(self, path: str = "logs/audit.jsonl") -> None:
        super().__init__(path)
        self.events: list[dict] = []
        self._events_lock = threading.Lock()

    def emit(self, event: AuditEvent) -> None:
        super().emit(event)
        with self._events_lock:
            self.events.append(json.loads(event.to_json_line()))
