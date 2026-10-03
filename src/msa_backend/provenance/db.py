"""SQLite provenance store.

Every run persists: input hash, full config snapshot, component versions,
per-column results and per-sequence coordinate maps. A run row is written
*before* computation with status ``running`` and flipped to ``completed``
or ``failed`` afterwards, so a crashed run never looks like a success.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from ..errors import RunNotFoundError

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    status TEXT NOT NULL,               -- running | completed | failed
    created_at TEXT NOT NULL,
    finished_at TEXT,
    input_sha256 TEXT NOT NULL,
    n_sequences INTEGER,
    n_columns INTEGER,
    total_weight REAL,
    config_json TEXT NOT NULL,
    versions_json TEXT NOT NULL,
    error_category TEXT,
    error_message TEXT
);
CREATE TABLE IF NOT EXISTS columns (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    column_index INTEGER NOT NULL,      -- 1-based
    distribution_json TEXT NOT NULL,
    entropy_bits REAL NOT NULL,
    information_content_bits REAL NOT NULL,
    effective_coverage REAL NOT NULL,
    gap_fraction REAL NOT NULL,
    consensus TEXT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY (run_id, column_index)
);
CREATE TABLE IF NOT EXISTS coordinate_maps (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    sequence_id TEXT NOT NULL,
    mapping_json TEXT NOT NULL,         -- JSON array of int|null
    PRIMARY KEY (run_id, sequence_id)
);
CREATE TABLE IF NOT EXISTS sequence_weights (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    sequence_id TEXT NOT NULL,
    weight REAL NOT NULL,
    cluster_id INTEGER NOT NULL,
    PRIMARY KEY (run_id, sequence_id)
);
"""


class ProvenanceStore:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: FastAPI executes sync endpoints in a
        # threadpool; the lock serialises access instead.
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(str(self.database_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    def _execute(self, *args, **kwargs) -> sqlite3.Cursor:
        with self._lock:
            return self._connection.execute(*args, **kwargs)

    def _commit(self) -> None:
        with self._lock:
            self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    # -- run lifecycle -----------------------------------------------------

    def begin_run(
        self,
        run_id: str,
        label: str,
        created_at: str,
        input_sha256: str,
        config_snapshot: dict,
        versions: dict,
    ) -> None:
        self._execute(
            "INSERT INTO runs (run_id, label, status, created_at, input_sha256,"
            " config_json, versions_json) VALUES (?, ?, 'running', ?, ?, ?, ?)",
            (
                run_id,
                label,
                created_at,
                input_sha256,
                json.dumps(config_snapshot, sort_keys=True),
                json.dumps(versions, sort_keys=True),
            ),
        )
        self._commit()

    def complete_run(
        self,
        run_id: str,
        finished_at: str,
        n_sequences: int,
        n_columns: int,
        total_weight: float,
    ) -> None:
        self._execute(
            "UPDATE runs SET status='completed', finished_at=?, n_sequences=?,"
            " n_columns=?, total_weight=? WHERE run_id=?",
            (finished_at, n_sequences, n_columns, total_weight, run_id),
        )
        self._commit()

    def fail_run(
        self, run_id: str, finished_at: str, error_category: str, error_message: str
    ) -> None:
        self._execute(
            "UPDATE runs SET status='failed', finished_at=?, error_category=?,"
            " error_message=? WHERE run_id=?",
            (finished_at, error_category, error_message, run_id),
        )
        self._commit()

    # -- result rows ---------------------------------------------------------

    def insert_column(self, run_id: str, column) -> None:
        self._execute(
            "INSERT INTO columns (run_id, column_index, distribution_json,"
            " entropy_bits, information_content_bits, effective_coverage,"
            " gap_fraction, consensus, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                column.column_index,
                json.dumps(column.distribution, sort_keys=True),
                column.entropy_bits,
                column.information_content_bits,
                column.effective_coverage,
                column.gap_fraction,
                column.consensus,
                column.status,
            ),
        )

    def insert_coordinate_map(self, run_id: str, sequence_id: str, mapping) -> None:
        self._execute(
            "INSERT INTO coordinate_maps (run_id, sequence_id, mapping_json)"
            " VALUES (?, ?, ?)",
            (run_id, sequence_id, json.dumps(list(mapping))),
        )

    def insert_weight(self, run_id: str, sequence_id: str, weight: float, cluster_id: int) -> None:
        self._execute(
            "INSERT INTO sequence_weights (run_id, sequence_id, weight, cluster_id)"
            " VALUES (?, ?, ?, ?)",
            (run_id, sequence_id, weight, cluster_id),
        )

    def commit(self) -> None:
        self._commit()

    # -- reads ---------------------------------------------------------------

    def get_run(self, run_id: str) -> dict:
        row = self._execute(
            "SELECT * FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise RunNotFoundError(f"run {run_id!r} not found")
        return dict(row)

    def list_runs(self) -> list[dict]:
        rows = self._execute(
            "SELECT * FROM runs ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_columns(self, run_id: str) -> list[dict]:
        self.get_run(run_id)  # raises RunNotFoundError when absent
        rows = self._execute(
            "SELECT * FROM columns WHERE run_id=? ORDER BY column_index", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_coordinate_maps(self, run_id: str) -> dict[str, list]:
        self.get_run(run_id)
        rows = self._execute(
            "SELECT sequence_id, mapping_json FROM coordinate_maps WHERE run_id=?",
            (run_id,),
        ).fetchall()
        return {r["sequence_id"]: json.loads(r["mapping_json"]) for r in rows}

    def get_weights(self, run_id: str) -> list[dict]:
        self.get_run(run_id)
        rows = self._execute(
            "SELECT sequence_id, weight, cluster_id FROM sequence_weights"
            " WHERE run_id=? ORDER BY cluster_id, sequence_id",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]
