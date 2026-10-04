"""SQLite-backed run provenance.

A digest run persists the *input identity* (raw sequence, enzyme, missed
cleavages), the *version context* (app / enzyme catalog / mass table), every
bond decision with its evidence, and every emitted fragment including the
mass status (EXACT / AMBIGUOUS / UNKNOWN) and nullable mass columns. Failed
runs are stored with status=FAILED and their categorized error code — errors
are never rewritten as success.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.errors import PersistenceError, RunNotFoundError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id              TEXT PRIMARY KEY,
    created_at          TEXT NOT NULL,
    status              TEXT NOT NULL,
    input_sequence      TEXT,
    sequence_length     INTEGER,
    enzyme              TEXT,
    missed_cleavages    INTEGER,
    error_code          TEXT,
    error_message       TEXT,
    versions_json       TEXT NOT NULL,
    duration_ms         REAL
);

CREATE TABLE IF NOT EXISTS bond_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    bond        INTEGER NOT NULL,
    p1          TEXT NOT NULL,
    p1_prime    TEXT NOT NULL,
    decision    TEXT NOT NULL,
    reason      TEXT NOT NULL,
    matched_rule TEXT
);

CREATE TABLE IF NOT EXISTS fragments (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL,
    fragment_id             TEXT NOT NULL,
    ord                     INTEGER NOT NULL,
    sequence                TEXT NOT NULL,
    start_residue           INTEGER NOT NULL,
    end_residue             INTEGER NOT NULL,
    length                  INTEGER NOT NULL,
    n_term_offset           INTEGER NOT NULL,
    c_term_offset           INTEGER NOT NULL,
    missed_cleavages        INTEGER NOT NULL,
    spanned_cut_bonds_json  TEXT NOT NULL,
    primary_indices_json    TEXT NOT NULL,
    is_nterminal            INTEGER NOT NULL,
    is_cterminal            INTEGER NOT NULL,
    mass_status             TEXT NOT NULL,
    neutral_mass            REAL,
    min_neutral_mass        REAL,
    max_neutral_mass        REAL,
    mhplus_mz               REAL,
    min_mhplus_mz           REAL,
    max_mhplus_mz           REAL,
    unknown_positions_json  TEXT NOT NULL,
    ambiguous_positions_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fragments_run ON fragments(run_id);
CREATE INDEX IF NOT EXISTS idx_decisions_run ON bond_decisions(run_id);
"""


class RunRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        directory = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(directory, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        try:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
        except sqlite3.Error as exc:
            raise PersistenceError(f"failed to initialize database: {exc}") from exc

    def save_success(
        self,
        run_id: str,
        raw_sequence: str,
        enzyme_name: str,
        missed_cleavages: int,
        result_dict: dict[str, Any],
        versions: dict[str, str],
        duration_ms: float,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO runs (run_id, created_at, status, input_sequence,
                       sequence_length, enzyme, missed_cleavages, error_code,
                       error_message, versions_json, duration_ms)
                       VALUES (?, ?, 'SUCCESS', ?, ?, ?, ?, NULL, NULL, ?, ?)""",
                    (
                        run_id, now, raw_sequence,
                        result_dict["sequence_length"], enzyme_name, missed_cleavages,
                        json.dumps(versions, sort_keys=True), duration_ms,
                    ),
                )
                conn.executemany(
                    """INSERT INTO bond_decisions (run_id, bond, p1, p1_prime,
                       decision, reason, matched_rule)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (run_id, d["bond"], d["p1"], d["p1_prime"], d["decision"],
                         d["reason"], d["matched_rule"])
                        for d in result_dict["bond_decisions"]
                    ],
                )
                conn.executemany(
                    """INSERT INTO fragments (run_id, fragment_id, ord, sequence,
                       start_residue, end_residue, length, n_term_offset,
                       c_term_offset, missed_cleavages, spanned_cut_bonds_json,
                       primary_indices_json, is_nterminal, is_cterminal,
                       mass_status, neutral_mass, min_neutral_mass, max_neutral_mass,
                       mhplus_mz, min_mhplus_mz, max_mhplus_mz,
                       unknown_positions_json, ambiguous_positions_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?, ?, ?, ?, ?)""",
                    [self._fragment_row(run_id, f) for f in result_dict["fragments"]],
                )
        except sqlite3.Error as exc:
            raise PersistenceError(f"failed to persist run {run_id}: {exc}") from exc

    @staticmethod
    def _fragment_row(run_id: str, f: dict[str, Any]) -> tuple:
        mass = f["mass"]
        return (
            run_id, f["fragment_id"], f["order"], f["sequence"],
            f["start"], f["end"], f["length"], f["n_term_offset"],
            f["c_term_offset"], f["missed_cleavages"],
            json.dumps(f["spanned_cut_bonds"]),
            json.dumps(f["primary_fragment_indices"]),
            int(f["is_nterminal"]), int(f["is_cterminal"]),
            mass["status"], mass["neutral_mass"], mass["min_neutral_mass"],
            mass["max_neutral_mass"], mass["mhplus_mz"],
            mass["min_mhplus_mz"], mass["max_mhplus_mz"],
            json.dumps(mass["unknown_positions"]),
            json.dumps(mass["ambiguous_positions"]),
        )

    def save_failure(
        self,
        run_id: str,
        raw_sequence: str | None,
        enzyme_name: str | None,
        missed_cleavages: int | None,
        error_code: str,
        error_message: str,
        versions: dict[str, str],
        duration_ms: float,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO runs (run_id, created_at, status, input_sequence,
                       sequence_length, enzyme, missed_cleavages, error_code,
                       error_message, versions_json, duration_ms)
                       VALUES (?, ?, 'FAILED', ?, NULL, ?, ?, ?, ?, ?, ?)""",
                    (
                        run_id, now, raw_sequence, enzyme_name, missed_cleavages,
                        error_code, error_message,
                        json.dumps(versions, sort_keys=True), duration_ms,
                    ),
                )
        except sqlite3.Error as exc:
            raise PersistenceError(f"failed to persist failure run {run_id}: {exc}") from exc

    def get_run(self, run_id: str) -> dict[str, Any]:
        try:
            with self._connect() as conn:
                run = conn.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if run is None:
                    raise RunNotFoundError(
                        f"no run with id {run_id!r}", {"run_id": run_id}
                    )
                decisions = conn.execute(
                    """SELECT bond, p1, p1_prime, decision, reason, matched_rule
                       FROM bond_decisions WHERE run_id = ? ORDER BY bond""",
                    (run_id,),
                ).fetchall()
                fragments = conn.execute(
                    "SELECT * FROM fragments WHERE run_id = ? ORDER BY ord", (run_id,)
                ).fetchall()
        except sqlite3.Error as exc:
            raise PersistenceError(f"failed to read run {run_id}: {exc}") from exc

        return {
            "run_id": run["run_id"],
            "created_at": run["created_at"],
            "status": run["status"],
            "input_sequence": run["input_sequence"],
            "parameters": {
                "enzyme": run["enzyme"],
                "missed_cleavages": run["missed_cleavages"],
            },
            "sequence_length": run["sequence_length"],
            "error": (
                {"code": run["error_code"], "message": run["error_message"]}
                if run["status"] == "FAILED"
                else None
            ),
            "versions": json.loads(run["versions_json"]),
            "duration_ms": run["duration_ms"],
            "bond_decisions": [dict(d) for d in decisions],
            "fragments": [self._fragment_from_row(f) for f in fragments],
        }

    @staticmethod
    def _fragment_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "fragment_id": row["fragment_id"],
            "order": row["ord"],
            "sequence": row["sequence"],
            "start": row["start_residue"],
            "end": row["end_residue"],
            "length": row["length"],
            "n_term_offset": row["n_term_offset"],
            "c_term_offset": row["c_term_offset"],
            "missed_cleavages": row["missed_cleavages"],
            "spanned_cut_bonds": json.loads(row["spanned_cut_bonds_json"]),
            "primary_fragment_indices": json.loads(row["primary_indices_json"]),
            "is_nterminal": bool(row["is_nterminal"]),
            "is_cterminal": bool(row["is_cterminal"]),
            "mass": {
                "status": row["mass_status"],
                "neutral_mass": row["neutral_mass"],
                "min_neutral_mass": row["min_neutral_mass"],
                "max_neutral_mass": row["max_neutral_mass"],
                "mhplus_mz": row["mhplus_mz"],
                "min_mhplus_mz": row["min_mhplus_mz"],
                "max_mhplus_mz": row["max_mhplus_mz"],
                "unknown_positions": json.loads(row["unknown_positions_json"]),
                "ambiguous_positions": json.loads(row["ambiguous_positions_json"]),
            },
        }

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT run_id, created_at, status, enzyme, missed_cleavages,
                              sequence_length, error_code
                       FROM runs ORDER BY created_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise PersistenceError(f"failed to list runs: {exc}") from exc
        return [dict(r) for r in rows]
