"""SQLite persistence for rounds, commitments, reveals, results, audit rows.

All writes go through this module; the state machine in ``service.py`` never
touches SQL. Timestamps are ISO-8601 UTC strings. Secrets (revealed values
and salts) are stored only in ``reveals`` because they are public evidence
after the reveal deadline — before that they never reach the server.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS rounds (
    round_id        TEXT PRIMARY KEY,
    participants    TEXT NOT NULL,          -- JSON array of participant ids
    commit_deadline TEXT NOT NULL,
    reveal_deadline TEXT NOT NULL,
    min_reveals     INTEGER NOT NULL,
    status          TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commitments (
    round_id        TEXT NOT NULL,
    participant_id  TEXT NOT NULL,
    commitment      TEXT NOT NULL,
    committed_at    TEXT NOT NULL,
    PRIMARY KEY (round_id, participant_id)
);
CREATE TABLE IF NOT EXISTS reveals (
    round_id        TEXT NOT NULL,
    participant_id  TEXT NOT NULL,
    value_hex       TEXT NOT NULL,
    salt_hex        TEXT NOT NULL,
    revealed_at     TEXT NOT NULL,
    PRIMARY KEY (round_id, participant_id)
);
CREATE TABLE IF NOT EXISTS results (
    round_id        TEXT PRIMARY KEY,
    seed_hex        TEXT,
    winner          TEXT,
    ranking         TEXT NOT NULL,          -- JSON array
    evidence        TEXT NOT NULL,          -- JSON document
    finalized_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    request_id      TEXT NOT NULL,
    event           TEXT NOT NULL,
    round_id        TEXT,
    participant_tag TEXT,
    decision        TEXT NOT NULL,
    reason          TEXT NOT NULL,
    detail          TEXT
);
"""

STATUS_COMMIT_OPEN = "COMMIT_OPEN"
STATUS_FINALIZED = "FINALIZED"
STATUS_ABORTED = "ABORTED"


class Store:
    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # -- rounds ---------------------------------------------------------
    def create_round(
        self,
        round_id: str,
        participants: list[str],
        commit_deadline: str,
        reveal_deadline: str,
        min_reveals: int,
        created_at: str,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO rounds VALUES (?,?,?,?,?,?,?)",
                (
                    round_id,
                    json.dumps(participants),
                    commit_deadline,
                    reveal_deadline,
                    min_reveals,
                    STATUS_COMMIT_OPEN,
                    created_at,
                ),
            )

    def get_round(self, round_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM rounds WHERE round_id = ?", (round_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "round_id": row["round_id"],
            "participants": json.loads(row["participants"]),
            "commit_deadline": row["commit_deadline"],
            "reveal_deadline": row["reveal_deadline"],
            "min_reveals": row["min_reveals"],
            "status": row["status"],
            "created_at": row["created_at"],
        }

    def set_status(self, round_id: str, status: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE rounds SET status = ? WHERE round_id = ?",
                (status, round_id),
            )

    # -- commitments ------------------------------------------------------
    def insert_commitment(
        self, round_id: str, participant_id: str, commitment: str, at: str
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO commitments VALUES (?,?,?,?)",
                (round_id, participant_id, commitment, at),
            )

    def get_commitment(self, round_id: str, participant_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT commitment FROM commitments WHERE round_id=? AND participant_id=?",
            (round_id, participant_id),
        ).fetchone()
        return None if row is None else row["commitment"]

    def list_commitments(self, round_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM commitments WHERE round_id=? ORDER BY participant_id",
            (round_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- reveals ----------------------------------------------------------
    def insert_reveal(
        self,
        round_id: str,
        participant_id: str,
        value_hex: str,
        salt_hex: str,
        at: str,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO reveals VALUES (?,?,?,?,?)",
                (round_id, participant_id, value_hex, salt_hex, at),
            )

    def get_reveal(self, round_id: str, participant_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM reveals WHERE round_id=? AND participant_id=?",
            (round_id, participant_id),
        ).fetchone()
        return None if row is None else dict(row)

    def list_reveals(self, round_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM reveals WHERE round_id=? ORDER BY participant_id",
            (round_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- results ----------------------------------------------------------
    def insert_result(
        self,
        round_id: str,
        seed_hex: str | None,
        winner: str | None,
        ranking: list[str],
        evidence: dict,
        at: str,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO results VALUES (?,?,?,?,?,?)",
                (
                    round_id,
                    seed_hex,
                    winner,
                    json.dumps(ranking),
                    json.dumps(evidence, sort_keys=True),
                    at,
                ),
            )

    def get_result(self, round_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM results WHERE round_id=?", (round_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "round_id": row["round_id"],
            "seed_hex": row["seed_hex"],
            "winner": row["winner"],
            "ranking": json.loads(row["ranking"]),
            "evidence": json.loads(row["evidence"]),
            "finalized_at": row["finalized_at"],
        }

    # -- audit ------------------------------------------------------------
    def append_audit(
        self,
        ts: str,
        request_id: str,
        event: str,
        decision: str,
        reason: str,
        round_id: str | None = None,
        participant_tag: str | None = None,
        detail: str | None = None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO audit_log"
                " (ts, request_id, event, round_id, participant_tag,"
                "  decision, reason, detail) VALUES (?,?,?,?,?,?,?,?)",
                (ts, request_id, event, round_id, participant_tag,
                 decision, reason, detail),
            )

    def list_audit(self, round_id: str | None = None) -> list[dict]:
        if round_id is None:
            rows = self._conn.execute(
                "SELECT * FROM audit_log ORDER BY id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM audit_log WHERE round_id=? ORDER BY id",
                (round_id,),
            ).fetchall()
        return [dict(r) for r in rows]
