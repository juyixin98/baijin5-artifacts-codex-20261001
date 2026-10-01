"""SQLite persistence for experiments, observations and run records.

Schema
------
* ``experiments``   - registered experiment metadata and covariate declarations.
* ``observations``  - one row per experimental unit (uploaded in batches).
* ``runs``          - every estimation run, completed or failed, with the full
                      JSON payload and a stable status. Failures are stored as
                      ``status='failed'`` rows, never as fake successes.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from app.core.errors import ErrorCode, ValidationError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id   TEXT PRIMARY KEY,
    description     TEXT NOT NULL,
    declarations    TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS observations (
    experiment_id   TEXT NOT NULL,
    unit_id         TEXT NOT NULL,
    treatment       INTEGER NOT NULL,
    outcome         REAL NOT NULL,
    covariates      TEXT NOT NULL,
    PRIMARY KEY (experiment_id, unit_id),
    FOREIGN KEY (experiment_id) REFERENCES experiments(experiment_id)
);

CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    experiment_id   TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('completed','failed')),
    error_code      TEXT,
    payload         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (experiment_id) REFERENCES experiments(experiment_id)
);

CREATE INDEX IF NOT EXISTS idx_runs_experiment ON runs(experiment_id);
CREATE INDEX IF NOT EXISTS idx_obs_experiment ON observations(experiment_id);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: sync FastAPI endpoints execute in worker
        # threads from a shared pool. An RLock serializes access to the single
        # connection so concurrent requests never interleave cursors.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ----- experiments -----------------------------------------------------

    def register_experiment(
        self,
        experiment_id: str,
        description: str,
        declarations: list[dict[str, Any]],
    ) -> None:
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO experiments(experiment_id, description, declarations) "
                    "VALUES (?, ?, ?)",
                    (experiment_id, description, json.dumps(declarations, sort_keys=True)),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ValidationError(
                    ErrorCode.SCHEMA_CONFLICT,
                    f"experiment {experiment_id!r} already registered",
                    {"experiment_id": experiment_id},
                ) from exc

    def get_experiment(self, experiment_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
            ).fetchone()

    def require_experiment(self, experiment_id: str) -> sqlite3.Row:
        row = self.get_experiment(experiment_id)
        if row is None:
            raise ValidationError(
                ErrorCode.UNKNOWN_EXPERIMENT,
                f"experiment {experiment_id!r} is not registered",
                {"experiment_id": experiment_id},
            )
        return row

    # ----- observations ----------------------------------------------------

    def insert_observations(
        self,
        experiment_id: str,
        rows: Iterable[tuple[str, int, float, dict[str, float | None]]],
    ) -> int:
        payload = [
            (experiment_id, uid, int(t), float(y), json.dumps(cov, sort_keys=True))
            for uid, t, y, cov in rows
        ]
        with self._lock:
            try:
                self._conn.executemany(
                    "INSERT INTO observations(experiment_id, unit_id, treatment, outcome, covariates) "
                    "VALUES (?, ?, ?, ?, ?)",
                    payload,
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ValidationError(
                    ErrorCode.DUPLICATE_UNIT,
                    "duplicate unit_id for this experiment",
                    {"experiment_id": experiment_id},
                ) from exc
        return len(payload)

    def load_observations(self, experiment_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._conn.execute(
                    "SELECT unit_id, treatment, outcome, covariates "
                    "FROM observations WHERE experiment_id = ? ORDER BY unit_id",
                    (experiment_id,),
                ).fetchall()
            )

    # ----- runs ------------------------------------------------------------

    def save_run(self, run_id: str, experiment_id: str, payload: dict[str, Any]) -> None:
        status = payload.get("status")
        if status not in ("completed", "failed"):
            raise ValueError(f"refusing to persist run with status={status!r}")
        error_code = payload.get("error_code") if status == "failed" else None
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs(run_id, experiment_id, status, error_code, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, experiment_id, status, error_code, json.dumps(payload, sort_keys=True)),
            )
            self._conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_runs(self, experiment_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM runs WHERE experiment_id = ? ORDER BY created_at",
                (experiment_id,),
            ).fetchall()
        return [json.loads(r["payload"]) for r in rows]
