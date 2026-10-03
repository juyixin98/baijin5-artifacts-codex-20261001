"""Run provenance persisted in SQLite.

Every distance computation is recorded as one `runs` row plus one
`pair_results` row per sequence pair, capturing the key intermediate
state (site counts, proportions) and the human-readable rationale for
each decision, so any run can be replayed and audited by run_id.

Idempotency / state conflicts: a client may supply its own run_id.
Reusing a run_id with an identical request hash returns the stored
result; reusing it with a *different* payload raises StateConflictError.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from .errors import ComputationFailureError, StateConflictError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    request_hash TEXT NOT NULL,
    request_json TEXT NOT NULL,
    model        TEXT NOT NULL,
    seed         INTEGER,
    status       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pair_results (
    run_id           TEXT NOT NULL REFERENCES runs(run_id),
    seq_a            TEXT NOT NULL,
    seq_b            TEXT NOT NULL,
    n_valid_sites    INTEGER NOT NULL,
    n_match          INTEGER NOT NULL,
    n_transition     INTEGER NOT NULL,
    n_transversion   INTEGER NOT NULL,
    p_distance       REAL,
    distance         REAL,
    status           TEXT NOT NULL,
    rationale_json   TEXT NOT NULL,
    bootstrap_json   TEXT,
    PRIMARY KEY (run_id, seq_a, seq_b)
);
"""


@dataclass(frozen=True)
class StoredRun:
    run_id: str
    request_hash: str
    request_json: str
    model: str
    seed: int | None
    status: str
    pair_results: tuple[dict, ...]


class RunStore:
    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        try:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            raise ComputationFailureError(
                "provenance_init_failed", f"cannot initialize provenance DB: {exc}"
            ) from exc

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def new_run_id() -> str:
        return uuid.uuid4().hex

    def get_run(self, run_id: str) -> StoredRun | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                return None
            pairs = conn.execute(
                "SELECT * FROM pair_results WHERE run_id = ? ORDER BY seq_a, seq_b",
                (run_id,),
            ).fetchall()
        return StoredRun(
            run_id=row["run_id"],
            request_hash=row["request_hash"],
            request_json=row["request_json"],
            model=row["model"],
            seed=row["seed"],
            status=row["status"],
            pair_results=tuple(dict(p) for p in pairs),
        )

    def create_run(
        self,
        run_id: str,
        request_hash: str,
        request_json: str,
        model: str,
        seed: int | None,
        status: str,
    ) -> None:
        """Insert a run row; enforce the idempotency contract on run_id reuse."""
        existing = self.get_run(run_id)
        if existing is not None:
            if existing.request_hash == request_hash:
                return  # idempotent replay: same payload, keep stored result
            raise StateConflictError(
                "run_id_payload_conflict",
                f"run_id {run_id!r} already exists with a different request payload",
                {"run_id": run_id},
            )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, request_hash, request_json, model, seed, status)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, request_hash, request_json, model, seed, status),
            )

    def record_pair_result(
        self,
        run_id: str,
        seq_a: str,
        seq_b: str,
        counts: tuple[int, int, int, int],  # n_valid, n_match, n_ti, n_tv
        p_distance: float | None,
        distance: float | None,
        status: str,
        rationale: list[str],
        bootstrap: dict | None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO pair_results (run_id, seq_a, seq_b, n_valid_sites,"
                " n_match, n_transition, n_transversion, p_distance, distance, status,"
                " rationale_json, bootstrap_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id, seq_a, seq_b,
                    counts[0], counts[1], counts[2], counts[3],
                    p_distance, distance, status,
                    json.dumps(rationale),
                    json.dumps(bootstrap) if bootstrap is not None else None,
                ),
            )
