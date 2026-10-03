"""Run provenance stored in SQLite.

Every started run is recorded with its exact input (JSON + sha256), its
parameters, every join decision, and either its result or its failure
category -- enough to replay the run bit-for-bit.

State-conflict contract
-----------------------
``create_run`` is idempotent: reusing a run_id with the *same* input hash
returns "exists_same". Reusing it with a *different* input hash raises
``StateConflictError``. A completed run is terminal; a failed run keeps its
record for postmortem and cannot be silently overwritten either.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from .errors import StateConflictError

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    input_json TEXT NOT NULL,
    params_json TEXT NOT NULL,
    n_taxa INTEGER NOT NULL,
    status TEXT NOT NULL,
    error_category TEXT,
    error_message TEXT
);
CREATE TABLE IF NOT EXISTS steps (
    run_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (run_id, step_index)
);
CREATE TABLE IF NOT EXISTS results (
    run_id TEXT PRIMARY KEY,
    newick TEXT NOT NULL,
    leaf_map_json TEXT NOT NULL,
    residuals_json TEXT NOT NULL,
    negative_events_json TEXT NOT NULL
);
"""


class ProvenanceStore:
    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- runs ---------------------------------------------------------------

    def create_run(
        self,
        run_id: str,
        input_sha256: str,
        input_json: str,
        params_json: str,
        n_taxa: int,
    ) -> str:
        """Returns "created" or "exists_same". Raises StateConflictError on
        run_id reuse with a different input hash."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT input_sha256 FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is not None:
                if row["input_sha256"] == input_sha256:
                    return "exists_same"
                raise StateConflictError(
                    f"run_id {run_id!r} already exists with a different input",
                    run_id=run_id,
                    details={"existing_sha256": row["input_sha256"], "submitted_sha256": input_sha256},
                )
            self._conn.execute(
                "INSERT INTO runs (run_id, created_at, input_sha256, input_json, params_json, n_taxa, status)"
                " VALUES (?, ?, ?, ?, ?, ?, 'running')",
                (run_id, datetime.now(timezone.utc).isoformat(), input_sha256,
                 input_json, params_json, n_taxa),
            )
        return "created"

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        error_category: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE runs SET status = ?, error_category = ?, error_message = ? WHERE run_id = ?",
                (status, error_category, error_message, run_id),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row is not None else None

    # -- steps / results ------------------------------------------------------

    def record_step(self, run_id: str, step_index: int, payload: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO steps (run_id, step_index, payload_json) VALUES (?, ?, ?)",
                (run_id, step_index, json.dumps(payload, sort_keys=True)),
            )

    def get_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload_json FROM steps WHERE run_id = ? ORDER BY step_index", (run_id,)
            ).fetchall()
        return [json.loads(r["payload_json"]) for r in rows]

    def record_result(
        self,
        run_id: str,
        *,
        newick: str,
        leaf_map: dict[str, Any],
        residuals: dict[str, Any],
        negative_events: list[dict[str, Any]],
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO results (run_id, newick, leaf_map_json, residuals_json, negative_events_json)"
                " VALUES (?, ?, ?, ?, ?)",
                (run_id, newick, json.dumps(leaf_map, sort_keys=True),
                 json.dumps(residuals), json.dumps(negative_events)),
            )

    def get_result(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM results WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "newick": row["newick"],
            "leaf_map": json.loads(row["leaf_map_json"]),
            "residuals": json.loads(row["residuals_json"]),
            "negative_events": json.loads(row["negative_events_json"]),
        }
