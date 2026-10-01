"""SQLite persistence for runs.

A small CRUD layer over one guarded connection. A single connection is used
for the store's lifetime (necessary for ``:memory:`` databases, which are
per-connection), serialised with a lock — adequate for the local, modest
write volume of this service. The schema is created at construction and
versioned with ``PRAGMA user_version``. Rows retain the full request and
response JSON so a run is auditable end to end.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    input_label   TEXT NOT NULL,
    status        TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    n_obs         INTEGER NOT NULL,
    cutoff        REAL NOT NULL,
    tau           REAL,
    request_json  TEXT NOT NULL,
    response_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at);
"""


class RunStore:
    def __init__(self, path: str) -> None:
        self._path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.execute("PRAGMA user_version = 1")
        self._conn.commit()

    def save(self, request_json: str, response_json: str) -> None:
        req = json.loads(request_json)
        resp = json.loads(response_json)
        estimate = resp.get("estimate") or {}
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO runs
                  (run_id, input_label, status, created_at, n_obs, cutoff,
                   tau, request_json, response_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resp["run_id"],
                    resp.get("input_label", ""),
                    resp["status"],
                    datetime.now(timezone.utc).isoformat(),
                    len(req.get("data", [])),
                    float(resp.get("cutoff", req.get("cutoff", 0.0))),
                    estimate.get("tau"),
                    request_json,
                    response_json,
                ),
            )
            self._conn.commit()

    def get(self, run_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list_runs(self, limit: int = 100, status: str | None = None) -> list[dict]:
        cols = (
            "run_id, input_label, status, created_at, n_obs, cutoff, tau"
        )
        with self._lock:
            if status is None:
                rows = self._conn.execute(
                    f"SELECT {cols} FROM runs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    f"SELECT {cols} FROM runs WHERE status = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
