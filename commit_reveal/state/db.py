"""SQLite persistence for rounds, commitments, reveals, results, and audit.

A single connection is shared per repository instance; the service layer
serializes access. All timestamps are integer Unix seconds supplied by the
caller (the clock is injected, never read here), which keeps the persistence
layer deterministic and testable.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS rounds (
    round_id        TEXT PRIMARY KEY,
    participants    TEXT NOT NULL,          -- JSON array of participant ids
    commit_deadline INTEGER NOT NULL,
    reveal_deadline INTEGER NOT NULL,
    status          TEXT NOT NULL,          -- OPEN | FROZEN | FINALIZED
    created_at      INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS commitments (
    round_id        TEXT NOT NULL,
    participant_id  TEXT NOT NULL,
    commitment      TEXT NOT NULL,
    received_at     INTEGER NOT NULL,
    PRIMARY KEY (round_id, participant_id)
);
CREATE TABLE IF NOT EXISTS reveals (
    round_id        TEXT NOT NULL,
    participant_id  TEXT NOT NULL,
    random_value    TEXT NOT NULL,          -- hex
    salt            TEXT NOT NULL,          -- hex
    received_at     INTEGER NOT NULL,
    PRIMARY KEY (round_id, participant_id)
);
CREATE TABLE IF NOT EXISTS results (
    round_id        TEXT PRIMARY KEY,
    seed            TEXT NOT NULL,
    winner          TEXT NOT NULL,
    evidence        TEXT NOT NULL,          -- JSON public evidence bundle
    finalized_at    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              INTEGER NOT NULL,
    request_id      TEXT NOT NULL,
    round_id        TEXT,
    event_type      TEXT NOT NULL,
    outcome         TEXT NOT NULL,          -- ACCEPTED | REJECTED | UNDETERMINED
    reason          TEXT,
    detail          TEXT                    -- JSON, secrets masked
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    """Open (and initialize) the database.

    ``check_same_thread=False`` because the ASGI server serves requests from
    a worker thread while the connection is created on the main thread.
    Callers sharing a connection across threads must serialize through a
    lock (see ``Repository``/``AuditLog``, which accept a shared lock).
    """
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
