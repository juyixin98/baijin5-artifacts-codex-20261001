"""SQLite provenance store.

One row per run: the full request hash, parameters, events, residuals and
the emitted Newick, so any result can be replayed or audited later.
``request_id`` is UNIQUE and backs the idempotency contract: same key + same
payload hash -> replay; same key + different hash -> STATE_CONFLICT.

The store is a thin boundary: it persists plain dicts and returns plain
dicts; it never raises service errors itself beyond :class:`StateConflictError`
semantics handled in the service layer.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    request_id  TEXT UNIQUE,
    input_hash  TEXT NOT NULL,
    params_json TEXT NOT NULL,
    status      TEXT NOT NULL,
    newick      TEXT,
    leaf_map_json    TEXT,
    residuals_json   TEXT,
    events_json      TEXT,
    error_json       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""


class RunStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save_run(self, record: dict[str, Any]) -> None:
        """Insert a completed (or failed) run record."""
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO runs (
                    run_id, request_id, input_hash, params_json, status,
                    newick, leaf_map_json, residuals_json, events_json, error_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["run_id"],
                    record.get("request_id"),
                    record["input_hash"],
                    json.dumps(record.get("params", {}), sort_keys=True),
                    record["status"],
                    record.get("newick"),
                    json.dumps(record.get("leaf_map")) if record.get("leaf_map") else None,
                    json.dumps(record.get("residuals")) if record.get("residuals") else None,
                    json.dumps(record.get("events", [])),
                    json.dumps(record.get("error")) if record.get("error") else None,
                ),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return self._decode(row) if row else None

    def find_by_request_id(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE request_id = ?", (request_id,)
            ).fetchone()
        return self._decode(row) if row else None

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        for key in ("params_json", "leaf_map_json", "residuals_json", "events_json", "error_json"):
            raw = record.pop(key)
            record[key.removesuffix("_json")] = json.loads(raw) if raw else None
        return record
