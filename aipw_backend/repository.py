"""SQLite-backed run registry.

Persists run lifecycle (queued -> running -> succeeded | failed) plus the
evidence bundle and structured error category, so a problem can be replayed
from disk by run id.  State transitions are guarded: recording a result for a
run that was never started, or finishing the same run twice, raises
:class:`StateConflictError` rather than silently overwriting history.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .errors import StateConflictError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    request     TEXT NOT NULL,
    evidence    TEXT,
    error_category TEXT,
    error_message  TEXT,
    error_details  TEXT
);
"""

_VALID_TRANSITIONS = {
    "queued": {"running"},
    "running": {"succeeded", "failed"},
    "succeeded": set(),
    "failed": set(),
}


class RunRepository:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "RunRepository":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def create(self, run_id: str, request_snapshot: dict[str, Any]) -> None:
        now = time.time()
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO runs (run_id, status, created_at, updated_at, request) "
                    "VALUES (?, 'queued', ?, ?, ?)",
                    (run_id, now, now, json.dumps(request_snapshot, sort_keys=True)),
                )
        except sqlite3.IntegrityError as exc:
            raise StateConflictError(
                f"run_id {run_id!r} already exists", details={"run_id": run_id}
            ) from exc

    def mark_running(self, run_id: str) -> None:
        self._transition(run_id, "running")

    def mark_succeeded(self, run_id: str, evidence: dict[str, Any]) -> None:
        self._transition(
            run_id,
            "succeeded",
            evidence=json.dumps(evidence, sort_keys=True),
        )

    def mark_failed(
        self,
        run_id: str,
        category: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._transition(
            run_id,
            "failed",
            error_category=category,
            error_message=message,
            error_details=json.dumps(details or {}, sort_keys=True),
        )

    def _transition(self, run_id: str, new_status: str, **fields: Any) -> None:
        with self._conn:
            row = self._conn.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise StateConflictError(
                    f"unknown run_id {run_id!r}", details={"run_id": run_id}
                )
            current = row["status"]
            if new_status not in _VALID_TRANSITIONS.get(current, set()):
                raise StateConflictError(
                    f"illegal transition {current!r} -> {new_status!r} for {run_id!r}",
                    details={"run_id": run_id, "from": current, "to": new_status},
                )
            assignments = ", ".join(f"{k} = ?" for k in fields)
            # Column order in the SQL is status, updated_at, then fields.
            params: list[Any] = [time.time(), *fields.values(), run_id]
            sql = (
                f"UPDATE runs SET status = ?, updated_at = ?"
                + (f", {assignments}" if fields else "")
                + " WHERE run_id = ?"
            )
            self._conn.execute(sql, [new_status, *params])

    def get(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        for key in ("request", "evidence", "error_details"):
            if d.get(key):
                d[key] = json.loads(d[key])
        return d

    def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT run_id, status, created_at, updated_at, error_category "
            "FROM runs ORDER BY created_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
