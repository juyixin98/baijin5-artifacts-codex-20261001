"""SQLite persistence: records, constraints, locks, and the run journal.

The run journal stores, per run: config, a full input snapshot (records,
constraints, locks), every pairwise decision with its reason, and the final
cluster assignment. That is enough to replay any run deterministically and
to diff two runs for affected entities.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..errors import ComputationError, InputValidationError
from ..models import ConstraintSet, Lock, Record

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
    record_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS constraints (
    kind TEXT NOT NULL CHECK (kind IN ('must_link', 'cannot_link')),
    left_id TEXT NOT NULL,
    right_id TEXT NOT NULL,
    PRIMARY KEY (kind, left_id, right_id)
);
CREATE TABLE IF NOT EXISTS locks (
    lock_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    config_json TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS assignments (
    run_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    PRIMARY KEY (run_id, record_id)
);
"""


class Store:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        try:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            raise ComputationError(f"failed to open store: {exc}") from exc

    def close(self) -> None:
        self._conn.close()

    # -- records ---------------------------------------------------------
    def upsert_records(self, records: list[Record]) -> None:
        with self._conn:
            for rec in records:
                self._conn.execute(
                    "INSERT OR REPLACE INTO records(record_id, payload) VALUES(?, ?)",
                    (rec.record_id, rec.model_dump_json()),
                )

    def load_records(self) -> list[Record]:
        rows = self._conn.execute(
            "SELECT payload FROM records ORDER BY record_id"
        ).fetchall()
        return [Record.model_validate_json(r["payload"]) for r in rows]

    # -- constraints -----------------------------------------------------
    def replace_constraints(self, constraints: ConstraintSet) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM constraints")
            for kind, pairs in (
                ("must_link", constraints.must_link),
                ("cannot_link", constraints.cannot_link),
            ):
                for a, b in pairs:
                    lo, hi = sorted((a, b))
                    self._conn.execute(
                        "INSERT OR IGNORE INTO constraints(kind, left_id, right_id)"
                        " VALUES(?, ?, ?)",
                        (kind, lo, hi),
                    )

    def load_constraints(self) -> ConstraintSet:
        rows = self._conn.execute(
            "SELECT kind, left_id, right_id FROM constraints ORDER BY kind, left_id"
        ).fetchall()
        must = [(r["left_id"], r["right_id"]) for r in rows if r["kind"] == "must_link"]
        cannot = [(r["left_id"], r["right_id"]) for r in rows if r["kind"] == "cannot_link"]
        return ConstraintSet(must_link=must, cannot_link=cannot)

    # -- locks -----------------------------------------------------------
    def put_lock(self, lock: Lock) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO locks(lock_id, payload) VALUES(?, ?)",
                (lock.lock_id, lock.model_dump_json()),
            )

    def delete_lock(self, lock_id: str) -> None:
        with self._conn:
            cur = self._conn.execute("DELETE FROM locks WHERE lock_id = ?", (lock_id,))
            if cur.rowcount == 0:
                raise InputValidationError(
                    "unknown lock_id", details={"lock_id": lock_id}
                )

    def load_locks(self) -> list[Lock]:
        rows = self._conn.execute("SELECT payload FROM locks ORDER BY lock_id").fetchall()
        return [Lock.model_validate_json(r["payload"]) for r in rows]

    # -- run journal -----------------------------------------------------
    def create_run(
        self,
        run_id: str,
        created_at: str,
        config: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO runs(run_id, created_at, config_json, snapshot_json, status)"
                " VALUES(?, ?, ?, ?, 'completed')",
                (run_id, created_at, json.dumps(config, sort_keys=True),
                 json.dumps(snapshot, sort_keys=True)),
            )

    def save_decisions(self, run_id: str, decisions: list[dict[str, Any]]) -> None:
        with self._conn:
            for seq, payload in enumerate(decisions):
                self._conn.execute(
                    "INSERT INTO decisions(run_id, seq, payload) VALUES(?, ?, ?)",
                    (run_id, seq, json.dumps(payload, sort_keys=True)),
                )

    def save_assignment(self, run_id: str, assignment: dict[str, str]) -> None:
        with self._conn:
            for rid, cid in sorted(assignment.items()):
                self._conn.execute(
                    "INSERT INTO assignments(run_id, record_id, cluster_id)"
                    " VALUES(?, ?, ?)",
                    (run_id, rid, cid),
                )

    def latest_run_id(self) -> str | None:
        row = self._conn.execute(
            "SELECT run_id FROM runs ORDER BY created_at DESC, run_id DESC LIMIT 1"
        ).fetchone()
        return row["run_id"] if row else None

    def previous_run_id(self, run_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT run_id FROM runs WHERE (created_at, run_id) <"
            " ((SELECT created_at FROM runs WHERE run_id = ?), ?)"
            " ORDER BY created_at DESC, run_id DESC LIMIT 1",
            (run_id, run_id),
        ).fetchone()
        return row["run_id"] if row else None

    def load_assignment(self, run_id: str) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT record_id, cluster_id FROM assignments WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        return {r["record_id"]: r["cluster_id"] for r in rows}

    def load_run(self, run_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise InputValidationError(
                "unknown run_id", details={"run_id": run_id}
            )
        decisions = self._conn.execute(
            "SELECT payload FROM decisions WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
        return {
            "run_id": row["run_id"],
            "created_at": row["created_at"],
            "status": row["status"],
            "config": json.loads(row["config_json"]),
            "snapshot": json.loads(row["snapshot_json"]),
            "decisions": [json.loads(d["payload"]) for d in decisions],
            "assignment": self.load_assignment(run_id),
        }
