"""SQLite persistence: message state, staged chunks, release area, audit.

Staging and release are separate tables with separate accessors - the
service layer is the only code allowed to move data from ``chunks``
(staging, access-controlled) into ``released`` (published plaintext),
and it does so in a single transaction after the whole stream has been
authenticated.  A crash between chunk authentication and the release
commit therefore cannot leak a partial message: on restart the message
is simply still ``open`` and finalization can be retried.
"""

from __future__ import annotations

import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    message_id       TEXT PRIMARY KEY,
    salt             BLOB NOT NULL,
    nonce_base       BLOB NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('open','released','failed')),
    created_at       REAL NOT NULL,
    released_at      REAL,
    plaintext_sha256 TEXT,
    chunk_count      INTEGER,
    fail_reason      TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    message_id TEXT NOT NULL REFERENCES messages(message_id),
    seq        INTEGER NOT NULL,
    request_id TEXT NOT NULL,
    final      INTEGER NOT NULL,
    pt_sha256  TEXT NOT NULL,
    ct         BLOB NOT NULL,
    aad        BLOB NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (message_id, seq)
);
CREATE TABLE IF NOT EXISTS released (
    message_id TEXT PRIMARY KEY REFERENCES messages(message_id),
    plaintext  BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    request_id TEXT NOT NULL,
    message_id TEXT,
    event      TEXT NOT NULL,
    decision   TEXT NOT NULL,
    reason     TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
"""


class Store:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.RLock()
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- messages ------------------------------------------------------

    def create_message(
        self, *, message_id: str, salt: bytes, nonce_base: bytes, created_at: float
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO messages (message_id, salt, nonce_base, status, created_at)"
                " VALUES (?,?,?,?,?)",
                (message_id, salt, nonce_base, "open", created_at),
            )

    def get_message(self, message_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
        return dict(row) if row else None

    def set_status(
        self,
        message_id: str,
        status: str,
        *,
        released_at: float | None = None,
        plaintext_sha256: str | None = None,
        chunk_count: int | None = None,
        fail_reason: str | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE messages SET status=?, released_at=COALESCE(?, released_at),"
                " plaintext_sha256=COALESCE(?, plaintext_sha256),"
                " chunk_count=COALESCE(?, chunk_count),"
                " fail_reason=COALESCE(?, fail_reason)"
                " WHERE message_id=?",
                (status, released_at, plaintext_sha256, chunk_count, fail_reason, message_id),
            )

    # -- staged chunks ---------------------------------------------------

    def insert_chunk(
        self,
        *,
        message_id: str,
        seq: int,
        request_id: str,
        final: bool,
        pt_sha256: str,
        ct: bytes,
        aad: bytes,
        created_at: float,
    ) -> None:
        """Insert a staged chunk.

        Raises ``sqlite3.IntegrityError`` when (message_id, seq) already
        exists - the service layer turns that into idempotent-replay or
        nonce-reuse-conflict handling.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO chunks (message_id, seq, request_id, final, pt_sha256,"
                " ct, aad, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (message_id, seq, request_id, int(final), pt_sha256, ct, aad, created_at),
            )

    def get_chunk(self, message_id: str, seq: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM chunks WHERE message_id=? AND seq=?",
                (message_id, seq),
            ).fetchone()
        return dict(row) if row else None

    def list_chunks(self, message_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM chunks WHERE message_id=? ORDER BY seq", (message_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    # -- release (single transaction) ------------------------------------

    def release(
        self,
        *,
        message_id: str,
        plaintext: bytes,
        plaintext_sha256: str,
        chunk_count: int,
        released_at: float,
    ) -> None:
        """Atomically publish plaintext and flip the message to released."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO released (message_id, plaintext) VALUES (?,?)",
                (message_id, plaintext),
            )
            self._conn.execute(
                "UPDATE messages SET status='released', released_at=?,"
                " plaintext_sha256=?, chunk_count=? WHERE message_id=?",
                (released_at, plaintext_sha256, chunk_count, message_id),
            )

    def get_released(self, message_id: str) -> bytes | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT plaintext FROM released WHERE message_id=?", (message_id,)
            ).fetchone()
        return bytes(row["plaintext"]) if row else None

    # -- audit -----------------------------------------------------------

    def insert_audit(
        self,
        *,
        ts: float,
        request_id: str,
        message_id: str | None,
        event: str,
        decision: str,
        reason: str,
        detail_json: str,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO audit (ts, request_id, message_id, event, decision,"
                " reason, detail_json) VALUES (?,?,?,?,?,?,?)",
                (ts, request_id, message_id, event, decision, reason, detail_json),
            )

    def list_audit(self, *, message_id: str | None = None) -> list[dict]:
        with self._lock:
            if message_id is None:
                rows = self._conn.execute("SELECT * FROM audit ORDER BY id").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM audit WHERE message_id=? ORDER BY id", (message_id,)
                ).fetchall()
        return [dict(r) for r in rows]
