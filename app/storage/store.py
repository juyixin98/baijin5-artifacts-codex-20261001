"""SQLite persistence for run provenance and the validation ledger.

Schema is intentionally explicit and small. Each digest run stores its full
input/parameters and its enumerated fragments, so a result can be retrieved
later by ``run_id`` and audited back to the exact sequence and rule that
produced it. A separate validation table records independently-verified
expectations and their verdict (never coerced to success).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id              TEXT PRIMARY KEY,
    created_at          TEXT NOT NULL,
    sequence            TEXT NOT NULL,
    sequence_length     INTEGER NOT NULL,
    enzyme_key          TEXT NOT NULL,
    enzyme_echo         TEXT NOT NULL,
    missed_cleavages    INTEGER NOT NULL,
    fixed_mods          TEXT NOT NULL,
    variable_mods       TEXT NOT NULL,
    fragment_count      INTEGER NOT NULL,
    has_ambiguous       INTEGER NOT NULL,
    has_unknown         INTEGER NOT NULL,
    mass_uncertain      INTEGER NOT NULL,
    warnings            TEXT NOT NULL,
    status              TEXT NOT NULL,
    service_version     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fragments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    fragment_index      INTEGER NOT NULL,
    start_pos           INTEGER NOT NULL,
    end_pos             INTEGER NOT NULL,
    sequence            TEXT NOT NULL,
    empty               INTEGER NOT NULL,
    n_terminal          INTEGER NOT NULL,
    c_terminal          INTEGER NOT NULL,
    cleavage_before     INTEGER,
    cleavage_after      INTEGER,
    missed_cleavages    INTEGER NOT NULL,
    internal_bonds      TEXT NOT NULL,
    mass_nominal        REAL NOT NULL,
    mass_min            REAL NOT NULL,
    mass_max            REAL NOT NULL,
    mass_status         TEXT NOT NULL,
    modification_count  INTEGER NOT NULL,
    payload             TEXT NOT NULL,
    UNIQUE(run_id, fragment_index)
);

CREATE TABLE IF NOT EXISTS validations (
    validation_id       TEXT PRIMARY KEY,
    run_id              TEXT REFERENCES runs(run_id) ON DELETE SET NULL,
    created_at          TEXT NOT NULL,
    case_name           TEXT NOT NULL,
    status              TEXT NOT NULL,
    expected_ref        TEXT NOT NULL,
    actual_summary      TEXT NOT NULL,
    mismatches          TEXT NOT NULL,
    service_version     TEXT NOT NULL
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DigestStore:
    """Thin synchronous SQLite wrapper. One short-lived connection per call."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        parent = Path(db_path).parent
        parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # -- writes -------------------------------------------------------------

    def save_run(self, run_record: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs (
                    run_id, created_at, sequence, sequence_length, enzyme_key,
                    enzyme_echo, missed_cleavages, fixed_mods, variable_mods,
                    fragment_count, has_ambiguous, has_unknown, mass_uncertain,
                    warnings, status, service_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_record["run_id"],
                    run_record.get("created_at", _utc_now()),
                    run_record["sequence"],
                    run_record["sequence_length"],
                    run_record["enzyme_key"],
                    json.dumps(run_record["enzyme_echo"], ensure_ascii=False),
                    run_record["missed_cleavages"],
                    json.dumps(run_record["fixed_mods"], ensure_ascii=False),
                    json.dumps(run_record["variable_mods"], ensure_ascii=False),
                    run_record["fragment_count"],
                    int(run_record["has_ambiguous"]),
                    int(run_record["has_unknown"]),
                    int(run_record["mass_uncertain"]),
                    json.dumps(run_record["warnings"], ensure_ascii=False),
                    run_record["status"],
                    run_record["service_version"],
                ),
            )
            conn.executemany(
                """
                INSERT OR REPLACE INTO fragments (
                    run_id, fragment_index, start_pos, end_pos, sequence, empty,
                    n_terminal, c_terminal, cleavage_before, cleavage_after,
                    missed_cleavages, internal_bonds, mass_nominal, mass_min,
                    mass_max, mass_status, modification_count, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_record["run_id"],
                        fp["fragment_index"],
                        fp["start"],
                        fp["end"],
                        fp["sequence"],
                        int(fp["empty"]),
                        int(fp["n_terminal"]),
                        int(fp["c_terminal"]),
                        fp["cleavage_before"],
                        fp["cleavage_after"],
                        fp["missed_cleavages"],
                        json.dumps(fp["internal_bonds"]),
                        fp["mass_nominal"],
                        fp["mass_min"],
                        fp["mass_max"],
                        fp["mass_status"],
                        fp["modification_count"],
                        json.dumps(fp["payload"], ensure_ascii=False),
                    )
                    for fp in run_record["fragments"]
                ],
            )

    def save_validation(self, record: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO validations (
                    validation_id, run_id, created_at, case_name, status,
                    expected_ref, actual_summary, mismatches, service_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["validation_id"],
                    record.get("run_id"),
                    record.get("created_at", _utc_now()),
                    record["case_name"],
                    record["status"],
                    json.dumps(record["expected_ref"], ensure_ascii=False),
                    json.dumps(record["actual_summary"], ensure_ascii=False),
                    json.dumps(record["mismatches"], ensure_ascii=False),
                    record["service_version"],
                ),
            )

    # -- reads --------------------------------------------------------------

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            run_row = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run_row is None:
                return None
            fragment_rows = conn.execute(
                "SELECT * FROM fragments WHERE run_id = ? ORDER BY fragment_index",
                (run_id,),
            ).fetchall()

        run = dict(run_row)
        for key in ("enzyme_echo", "fixed_mods", "variable_mods", "warnings"):
            run[key] = json.loads(run[key])
        run["fragments"] = [dict(row) for row in fragment_rows]
        for row in run["fragments"]:
            row["internal_bonds"] = json.loads(row["internal_bonds"])
            row["payload"] = json.loads(row["payload"])
        return run

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT run_id, created_at, enzyme_key, sequence_length, "
                "fragment_count, status FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_validation(self, validation_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM validations WHERE validation_id = ?",
                (validation_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in ("expected_ref", "actual_summary", "mismatches"):
            result[key] = json.loads(result[key])
        return result
