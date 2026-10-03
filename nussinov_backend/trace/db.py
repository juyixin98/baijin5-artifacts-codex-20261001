"""SQLite persistence for request lineage / provenance."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    request_id              TEXT PRIMARY KEY,
    created_at              TEXT NOT NULL,
    finished_at             TEXT NOT NULL,
    status                  TEXT NOT NULL,
    source                  TEXT NOT NULL,
    raw_sequence            TEXT,
    sequence                TEXT,
    sequence_length         INTEGER,
    fasta_header            TEXT,
    min_loop_length         INTEGER NOT NULL,
    pseudoknots_supported   INTEGER NOT NULL,
    allowed_pairs           TEXT NOT NULL,
    alternatives_requested  INTEGER NOT NULL,
    alternatives_limit      INTEGER,
    optimum                 INTEGER,
    primary_dot_bracket     TEXT,
    primary_pair_count      INTEGER,
    error_category          TEXT,
    error_message           TEXT,
    algorithm_name          TEXT NOT NULL,
    algorithm_version       TEXT NOT NULL,
    model_scope             TEXT NOT NULL,
    processing_location     TEXT NOT NULL,
    duration_ms             REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS processing_steps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT NOT NULL REFERENCES requests(request_id),
    step_order  INTEGER NOT NULL,
    step_name   TEXT NOT NULL,
    outcome     TEXT NOT NULL,
    detail      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS structures (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      TEXT NOT NULL REFERENCES requests(request_id),
    structure_order INTEGER NOT NULL,
    is_primary      INTEGER NOT NULL,
    pair_count      INTEGER NOT NULL,
    dot_bracket     TEXT NOT NULL,
    pairs_json      TEXT NOT NULL,
    pair_table_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS request_warnings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT NOT NULL REFERENCES requests(request_id),
    warning_order INTEGER NOT NULL,
    warning     TEXT NOT NULL
);
"""


class Database:
    """Thread-safe wrapper around a single SQLite connection."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)

    @property
    def path(self) -> Path:
        return self._path

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def transaction(self) -> "Transaction":
        return Transaction(self)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class Transaction:
    def __init__(self, db: Database) -> None:
        self._db = db

    def __enter__(self) -> Database:
        self._db.execute("BEGIN IMMEDIATE")
        return self._db

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self._db.execute("COMMIT")
        else:
            self._db.execute("ROLLBACK")
