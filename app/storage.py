"""SQLite persistence for analysis requests and their runs.

One file-based database (path from settings) holds:

* ``requests``  - one row per API request, keyed by the correlated request id
* ``analyses``  - one row per statistical result (p-value test or inversion),
                  linked back to its request

The full result document is stored as JSON so the exact response can be
replayed from the database alone.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from app.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    request_id      TEXT PRIMARY KEY,
    created_at      REAL NOT NULL,
    n_pairs         INTEGER NOT NULL,
    alpha           REAL,
    endpoint        TEXT NOT NULL,
    status          TEXT NOT NULL,
    payload_json    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      TEXT NOT NULL REFERENCES requests(request_id),
    created_at      REAL NOT NULL,
    kind            TEXT NOT NULL,
    method          TEXT NOT NULL,
    effect          REAL,
    result_json     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_analyses_request ON analyses(request_id);
"""


class Database:
    """Thread-safe thin wrapper around a single SQLite connection."""

    def __init__(self, path: str):
        self.path = path
        db_path = Path(path)
        if db_path.parent and str(db_path.parent) not in ("", "."):
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------
    def record_request(self, request_id: str, n_pairs: int,
                       endpoint: str, payload: dict[str, Any],
                       alpha: float | None = None,
                       status: str = "received") -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO requests
                    (request_id, created_at, n_pairs, alpha, endpoint,
                     status, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    time.time(),
                    n_pairs,
                    alpha,
                    endpoint,
                    status,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            self._conn.commit()

    def update_request_status(self, request_id: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE requests SET status = ? WHERE request_id = ?",
                (status, request_id),
            )
            self._conn.commit()

    def record_analysis(self, request_id: str, kind: str, method: str,
                        result: dict[str, Any],
                        effect: float | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO analyses
                    (request_id, created_at, kind, method, effect, result_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    time.time(),
                    kind,
                    method,
                    effect,
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    # ------------------------------------------------------------------
    def get_request(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            req = self._conn.execute(
                "SELECT * FROM requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if req is None:
                return None
            rows = self._conn.execute(
                "SELECT * FROM analyses WHERE request_id = ? ORDER BY id",
                (request_id,),
            ).fetchall()
        return {
            "request": {
                "request_id": req["request_id"],
                "created_at": req["created_at"],
                "n_pairs": req["n_pairs"],
                "alpha": req["alpha"],
                "endpoint": req["endpoint"],
                "status": req["status"],
                "payload": json.loads(req["payload_json"]),
            },
            "analyses": [
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "method": row["method"],
                    "effect": row["effect"],
                    "created_at": row["created_at"],
                    "result": json.loads(row["result_json"]),
                }
                for row in rows
            ],
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_db: Database | None = None
_db_lock = threading.Lock()


def get_database() -> Database:
    """Process-wide database handle (FastAPI dependency / tests share it)."""
    global _db
    with _db_lock:
        if _db is None:
            _db = Database(settings.database_path)
        return _db


def reset_database_for_tests(path: str) -> Database:
    """Test hook: point the global handle at a fresh database file."""
    global _db
    with _db_lock:
        if _db is not None:
            _db.close()
        Path(path).unlink(missing_ok=True)
        _db = Database(path)
        return _db
