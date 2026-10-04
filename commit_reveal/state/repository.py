"""Repository: typed access over the SQLite schema. No protocol rules here."""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
from dataclasses import dataclass


def _synchronized(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


@dataclass(frozen=True)
class RoundRow:
    round_id: str
    participants: list[str]
    commit_deadline: int
    reveal_deadline: int
    status: str
    created_at: int


@dataclass(frozen=True)
class CommitmentRow:
    round_id: str
    participant_id: str
    commitment: str
    received_at: int


@dataclass(frozen=True)
class RevealRow:
    round_id: str
    participant_id: str
    random_value: str
    salt: str
    received_at: int


@dataclass(frozen=True)
class ResultRow:
    round_id: str
    seed: str
    winner: str
    evidence: dict
    finalized_at: int


class Repository:
    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock | None = None):
        self._conn = conn
        # Shared with AuditLog by the app factory so all DB access on this
        # connection is serialized across server worker threads.
        self._lock = lock or threading.RLock()

    # ---- rounds ---------------------------------------------------------
    @_synchronized
    def insert_round(
        self,
        round_id: str,
        participants: list[str],
        commit_deadline: int,
        reveal_deadline: int,
        status: str,
        created_at: int,
    ) -> None:
        self._conn.execute(
            "INSERT INTO rounds VALUES (?, ?, ?, ?, ?, ?)",
            (
                round_id,
                json.dumps(participants),
                commit_deadline,
                reveal_deadline,
                status,
                created_at,
            ),
        )
        self._conn.commit()

    @_synchronized
    def get_round(self, round_id: str) -> RoundRow | None:
        row = self._conn.execute(
            "SELECT * FROM rounds WHERE round_id = ?", (round_id,)
        ).fetchone()
        if row is None:
            return None
        return RoundRow(
            round_id=row["round_id"],
            participants=json.loads(row["participants"]),
            commit_deadline=row["commit_deadline"],
            reveal_deadline=row["reveal_deadline"],
            status=row["status"],
            created_at=row["created_at"],
        )

    @_synchronized
    def update_round_status(self, round_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE rounds SET status = ? WHERE round_id = ?", (status, round_id)
        )
        self._conn.commit()

    # ---- commitments ------------------------------------------------------
    @_synchronized
    def insert_commitment(self, c: CommitmentRow) -> None:
        self._conn.execute(
            "INSERT INTO commitments VALUES (?, ?, ?, ?)",
            (c.round_id, c.participant_id, c.commitment, c.received_at),
        )
        self._conn.commit()

    @_synchronized
    def get_commitment(self, round_id: str, participant_id: str) -> CommitmentRow | None:
        row = self._conn.execute(
            "SELECT * FROM commitments WHERE round_id = ? AND participant_id = ?",
            (round_id, participant_id),
        ).fetchone()
        return CommitmentRow(**dict(row)) if row else None

    @_synchronized
    def list_commitments(self, round_id: str) -> list[CommitmentRow]:
        rows = self._conn.execute(
            "SELECT * FROM commitments WHERE round_id = ? ORDER BY participant_id",
            (round_id,),
        ).fetchall()
        return [CommitmentRow(**dict(r)) for r in rows]

    # ---- reveals ----------------------------------------------------------
    @_synchronized
    def insert_reveal(self, r: RevealRow) -> None:
        self._conn.execute(
            "INSERT INTO reveals VALUES (?, ?, ?, ?, ?)",
            (r.round_id, r.participant_id, r.random_value, r.salt, r.received_at),
        )
        self._conn.commit()

    @_synchronized
    def get_reveal(self, round_id: str, participant_id: str) -> RevealRow | None:
        row = self._conn.execute(
            "SELECT * FROM reveals WHERE round_id = ? AND participant_id = ?",
            (round_id, participant_id),
        ).fetchone()
        return RevealRow(**dict(row)) if row else None

    @_synchronized
    def list_reveals(self, round_id: str) -> list[RevealRow]:
        rows = self._conn.execute(
            "SELECT * FROM reveals WHERE round_id = ? ORDER BY participant_id",
            (round_id,),
        ).fetchall()
        return [RevealRow(**dict(r)) for r in rows]

    # ---- results ----------------------------------------------------------
    @_synchronized
    def insert_result(self, result: ResultRow) -> None:
        self._conn.execute(
            "INSERT INTO results VALUES (?, ?, ?, ?, ?)",
            (
                result.round_id,
                result.seed,
                result.winner,
                json.dumps(result.evidence, sort_keys=True),
                result.finalized_at,
            ),
        )
        self._conn.commit()

    @_synchronized
    def get_result(self, round_id: str) -> ResultRow | None:
        row = self._conn.execute(
            "SELECT * FROM results WHERE round_id = ?", (round_id,)
        ).fetchone()
        if row is None:
            return None
        return ResultRow(
            round_id=row["round_id"],
            seed=row["seed"],
            winner=row["winner"],
            evidence=json.loads(row["evidence"]),
            finalized_at=row["finalized_at"],
        )
