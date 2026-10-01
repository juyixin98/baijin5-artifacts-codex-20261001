"""SQLite persistence for experiment runs.

Results are stored as canonical JSON keyed by run_id; a thin relational
projection (runs table) allows listing and lookup without parsing blobs.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from ..core.contracts import ErrorCode, EstimationError, RunResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,
    data_fingerprint  TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    experiment_name   TEXT NOT NULL,
    n_rows            INTEGER NOT NULL,
    unadjusted_estimate REAL,
    unadjusted_se       REAL,
    cuped_estimate      REAL,
    cuped_se            REAL,
    lin_estimate        REAL,
    lin_se              REAL,
    result_json       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_name ON runs(experiment_name);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at);
"""


class RunStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Sync endpoints run in a shared thread pool, so the single
        # connection is guarded; WAL plus the lock serialise writers.
        self._lock = threading.Lock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def save(self, result: RunResult) -> None:
        blob = json.dumps(result.to_dict(), sort_keys=True, ensure_ascii=False)
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO runs (
                    run_id, data_fingerprint, created_at, experiment_name,
                    n_rows, unadjusted_estimate, unadjusted_se,
                    cuped_estimate, cuped_se, lin_estimate, lin_se, result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.run_id,
                    result.data_fingerprint,
                    result.created_at,
                    result.experiment_name,
                    result.n_rows,
                    result.unadjusted.estimate,
                    result.unadjusted.se,
                    result.cuped.estimate,
                    result.cuped.se,
                    result.lin.estimate,
                    result.lin.se,
                    blob,
                ),
            )

    def get(self, run_id: str) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT result_json FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise EstimationError(
                ErrorCode.RUN_NOT_FOUND,
                f"no stored run with run_id={run_id!r}",
                {"run_id": run_id},
            )
        return json.loads(row["result_json"])

    def list_runs(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT run_id, data_fingerprint, created_at, experiment_name,
                       n_rows, unadjusted_estimate, unadjusted_se,
                       cuped_estimate, cuped_se, lin_estimate, lin_se
                FROM runs ORDER BY created_at DESC, rowid DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
