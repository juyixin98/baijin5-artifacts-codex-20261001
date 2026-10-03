"""SQLite-backed run provenance.

Every computed run is recorded with its run id, input hash, parameters,
intermediate state (site counts, observed fractions) and final verdict, so a
reported result can be replayed and audited. Duplicate run ids are a state
conflict, never an overwrite.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any

from .errors import StateConflictError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    model         TEXT NOT NULL,
    seed          INTEGER NOT NULL,
    n_replicates  INTEGER NOT NULL,
    alpha         REAL NOT NULL,
    input_sha256  TEXT NOT NULL,
    status        TEXT NOT NULL,
    result_json   TEXT NOT NULL,
    intermediates_json TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    created_at: str  # ISO-8601 UTC
    model: str
    seed: int
    n_replicates: int
    alpha: float
    input_sha256: str
    status: str
    result: dict[str, Any]
    intermediates: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)
    conn.commit()


def connect(db_path: str) -> sqlite3.Connection:
    # check_same_thread=False: the FastAPI layer serves requests from worker
    # threads; writes are serialized by SQLite's own locking.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def record_run(conn: sqlite3.Connection, record: RunRecord) -> None:
    """Insert a run record; an existing run_id is a state conflict."""
    try:
        conn.execute(
            """
            INSERT INTO runs (run_id, created_at, model, seed, n_replicates,
                              alpha, input_sha256, status, result_json,
                              intermediates_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.run_id,
                record.created_at,
                record.model,
                record.seed,
                record.n_replicates,
                record.alpha,
                record.input_sha256,
                record.status,
                json.dumps(record.result, sort_keys=True),
                json.dumps(record.intermediates, sort_keys=True),
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise StateConflictError(
            "run id already recorded",
            detail={"run_id": record.run_id},
        ) from exc


def get_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    return {
        "run_id": row["run_id"],
        "created_at": row["created_at"],
        "model": row["model"],
        "seed": row["seed"],
        "n_replicates": row["n_replicates"],
        "alpha": row["alpha"],
        "input_sha256": row["input_sha256"],
        "status": row["status"],
        "result": json.loads(row["result_json"]),
        "intermediates": json.loads(row["intermediates_json"]),
    }
