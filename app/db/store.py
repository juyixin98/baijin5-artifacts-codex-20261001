"""SQLite persistence for stream state, segment receipts and audit trail.

The database stores only metadata and *hashes* of ciphertext; plaintext and
keys never touch the database.  WAL mode and a single guarded connection make
the store safe across the FastAPI threadpool, and durable enough that a new
process can reconstruct every in-flight stream after a crash.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS streams (
    message_id      TEXT PRIMARY KEY,
    key_id          TEXT NOT NULL,
    total_segments  INTEGER NOT NULL,
    total_len       INTEGER NOT NULL,
    status          TEXT NOT NULL,            -- receiving|released|failed|interrupted
    received_count  INTEGER NOT NULL DEFAULT 0,
    byte_count      INTEGER NOT NULL DEFAULT 0,
    max_seqno       INTEGER NOT NULL DEFAULT -1,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    released_at     REAL
);

CREATE TABLE IF NOT EXISTS segments (
    message_id      TEXT NOT NULL,
    seqno           INTEGER NOT NULL,
    is_final        INTEGER NOT NULL,
    nonce_b64       TEXT NOT NULL,
    ct_hash         TEXT NOT NULL,            -- sha256 of frame ciphertext
    ct_len          INTEGER NOT NULL,
    plaintext_len   INTEGER NOT NULL,
    staged_path     TEXT NOT NULL,
    verified_at     REAL NOT NULL,
    PRIMARY KEY (message_id, seqno),
    FOREIGN KEY (message_id) REFERENCES streams(message_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    message_id  TEXT,
    kind        TEXT NOT NULL,
    category    TEXT NOT NULL,
    request_id  TEXT,
    message     TEXT NOT NULL,
    state_json  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_segments_stream ON segments(message_id);
CREATE INDEX IF NOT EXISTS idx_audit_stream ON audit_log(message_id);
"""

# Terminal stream statuses.
RECEIVING = "receiving"
RELEASED = "released"
FAILED = "failed"
INTERRUPTED = "interrupted"
ACTIVE_STATUSES = {RECEIVING, INTERRUPTED}


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- streams ----------------------------------------------------------

    def begin_stream(self, message_id: str, key_id: str, total_segments: int,
                     total_len: int) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO streams(message_id, key_id, total_segments, "
                    "total_len, status, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (message_id, key_id, total_segments, total_len,
                     RECEIVING, now, now))
            except sqlite3.IntegrityError as exc:
                raise _integrity(exc)
            return self.get_stream(message_id)

    def get_stream(self, message_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM streams WHERE message_id=?",
                (message_id,)).fetchone()
            return dict(row) if row else None

    def mark_status(self, message_id: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE streams SET status=?, updated_at=? WHERE message_id=?",
                (status, time.time(), message_id))

    def mark_interrupted_on_startup(self) -> int:
        """Crash recovery: receiving streams died with the old process."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE streams SET status=?, updated_at=? "
                "WHERE status IN (?, ?)",
                (INTERRUPTED, time.time(), RECEIVING, INTERRUPTED))
            return cur.rowcount

    def resume_stream(self, message_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE streams SET status=?, updated_at=? "
                "WHERE message_id=? AND status=?",
                (RECEIVING, time.time(), message_id, INTERRUPTED))

    def bump_stream(self, message_id: str, seqno: int,
                    plaintext_len: int) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                "UPDATE streams SET received_count=received_count+1, "
                "byte_count=byte_count+?, max_seqno=MAX(max_seqno,?), "
                "updated_at=? WHERE message_id=?",
                (plaintext_len, seqno, time.time(), message_id))
            return self.get_stream(message_id)

    def mark_released(self, message_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE streams SET status=?, released_at=?, updated_at=? "
                "WHERE message_id=?",
                (RELEASED, time.time(), time.time(), message_id))

    def list_streams(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT * FROM streams ORDER BY created_at")]

    # ---- segments ---------------------------------------------------------

    def get_segment(self, message_id: str, seqno: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM segments WHERE message_id=? AND seqno=?",
                (message_id, seqno)).fetchone()
            return dict(row) if row else None

    def list_segments(self, message_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT * FROM segments WHERE message_id=? ORDER BY seqno",
                (message_id,))]

    def has_segment(self, message_id: str, seqno: int) -> bool:
        return self.get_segment(message_id, seqno) is not None

    def add_segment(self, message_id: str, seqno: int, is_final: bool,
                    nonce: bytes, ct_hash: str, ct_len: int,
                    plaintext_len: int, staged_path: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO segments(message_id, seqno, is_final, nonce_b64, "
                "ct_hash, ct_len, plaintext_len, staged_path, verified_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (message_id, seqno, 1 if is_final else 0,
                 _b64(nonce), ct_hash, ct_len, plaintext_len, staged_path,
                 time.time()))

    def drop_segment(self, message_id: str, seqno: int) -> None:
        """Remove one segment receipt (crash-recovery reconciliation)."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM segments WHERE message_id=? AND seqno=?",
                (message_id, seqno))

    def delete_segments(self, message_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM segments WHERE message_id=?", (message_id,))

    # ---- audit -------------------------------------------------------------

    def audit(self, message_id: str | None, kind: str, category: str,
              request_id: str | None, message: str,
              state: dict[str, Any]) -> None:
        from ..core.audit import safe_state
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_log(ts, message_id, kind, category, "
                "request_id, message, state_json) VALUES (?,?,?,?,?,?,?)",
                (time.time(), message_id, kind, category, request_id, message,
                 json.dumps(safe_state(state), sort_keys=True)))

    def list_audit(self, message_id: str | None = None,
                   limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            if message_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
                    (limit,))
            else:
                rows = self._conn.execute(
                    "SELECT * FROM audit_log WHERE message_id=? "
                    "ORDER BY id DESC LIMIT ?", (message_id, limit))
            out: list[dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                d["state"] = json.loads(d.pop("state_json"))
                out.append(d)
            return out

    def iter_streams(self) -> Iterable[dict[str, Any]]:
        return self.list_streams()


def _b64(raw: bytes) -> str:
    import base64
    return base64.b64encode(raw).decode()


def _integrity(exc: sqlite3.IntegrityError) -> Exception:
    from ..core.errors import StateError
    return StateError("stream already exists", detail=str(exc))
