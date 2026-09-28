"""SQLite-backed run registry and replayable event log.

State machine for every run id::

    pending -> running -> succeeded
                        \\-> failed (category in input/state/resource/computation)

Illegal transitions raise ``StateConflictError`` so a reused run id or a
double-execution cannot silently overwrite a previous result. The event table
records, in order: run id, monotonic event number, timestamp, state, error
category and a JSON snapshot of the key intermediate state (fold sizes, OOF
summary, point, se, failure reason). That row set is exactly what is needed
to replay and explain a decision.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .contract import RunState, StateConflictError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    state       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    seed        INTEGER NOT NULL,
    result_json TEXT,
    error_json  TEXT
);
CREATE TABLE IF NOT EXISTS events (
    run_id   TEXT NOT NULL,
    seq      INTEGER NOT NULL,
    at       REAL NOT NULL,
    state    TEXT NOT NULL,
    category TEXT,
    detail   TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
"""

_LEGAL_TRANSITIONS: dict[RunState, tuple[RunState, ...]] = {
    RunState.PENDING: (RunState.RUNNING,),
    RunState.RUNNING: (RunState.SUCCEEDED, RunState.FAILED),
    RunState.SUCCEEDED: (),
    RunState.FAILED: (),
}


class JobStore:
    """SQLite registry. Safe across the FastAPI worker/test threads:

    the connection is opened with ``check_same_thread=False`` and every
    multi-statement transaction takes ``self._lock``. Reads are serialized by
    the same lock so callers never see a half-written state transition.
    """

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "JobStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ------------------------------------------------------------------ #
    def create_run(self, *, seed: int, run_id: str | None = None) -> str:
        run_id = run_id or f"run-{uuid.uuid4()}"
        now = time.time()
        try:
            with self._tx() as c:
                c.execute(
                    "INSERT INTO runs(run_id, state, created_at, updated_at, seed) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (run_id, RunState.PENDING.value, now, now, int(seed)),
                )
                c.execute(
                    "INSERT INTO events(run_id, seq, at, state, category, detail) "
                    "VALUES (?, 0, ?, ?, ?, ?)",
                    (run_id, now, RunState.PENDING.value, None,
                     json.dumps({"seed": int(seed)})),
                )
        except sqlite3.IntegrityError as exc:
            raise StateConflictError(
                "run id already exists; supply a unique run id",
                details={"run_id": run_id},
            ) from exc
        return run_id

    def _transition(self, run_id: str, target: RunState) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise StateConflictError("unknown run id", details={"run_id": run_id})
        current = RunState(row["state"])
        if target not in _LEGAL_TRANSITIONS[current]:
            raise StateConflictError(
                "illegal run state transition",
                details={"run_id": run_id, "from": current.value,
                         "to": target.value},
            )

    def mark_running(self, run_id: str, detail: dict[str, Any]) -> None:
        self._transition(run_id, RunState.RUNNING)
        now = time.time()
        seq = self._next_seq(run_id)
        with self._tx() as c:
            c.execute("UPDATE runs SET state=?, updated_at=? WHERE run_id=?",
                      (RunState.RUNNING.value, now, run_id))
            c.execute(
                "INSERT INTO events(run_id, seq, at, state, category, detail) "
                "VALUES (?, ?, ?, ?, NULL, ?)",
                (run_id, seq, now, RunState.RUNNING.value, json.dumps(detail)),
            )

    def mark_succeeded(self, run_id: str, result: dict[str, Any]) -> None:
        self._transition(run_id, RunState.SUCCEEDED)
        now = time.time()
        seq = self._next_seq(run_id)
        with self._tx() as c:
            c.execute(
                "UPDATE runs SET state=?, updated_at=?, result_json=? WHERE run_id=?",
                (RunState.SUCCEEDED.value, now, json.dumps(result), run_id),
            )
            c.execute(
                "INSERT INTO events(run_id, seq, at, state, category, detail) "
                "VALUES (?, ?, ?, ?, NULL, ?)",
                (run_id, seq, now, RunState.SUCCEEDED.value,
                 json.dumps(_result_summary(result))),
            )

    def mark_failed(self, run_id: str, category: str, message: str,
                    detail: dict[str, Any]) -> None:
        # A run that failed input validation before "running" moves pending->
        # failed directly; relax that single edge by checking explicitly.
        row = self._conn.execute(
            "SELECT state FROM runs WHERE run_id=?", (run_id,)).fetchone()
        current = RunState(row["state"]) if row is not None else None
        target = RunState.FAILED
        if current is RunState.RUNNING or current is RunState.PENDING:
            allowed = True
        else:
            allowed = target in _LEGAL_TRANSITIONS.get(current or RunState.PENDING, ())
        if not allowed:
            raise StateConflictError(
                "cannot fail a run that is already terminal",
                details={"run_id": run_id, "state": current.value if current else None},
            )
        now = time.time()
        seq = self._next_seq(run_id)
        error = {"category": category, "message": message, "details": detail}
        with self._tx() as c:
            c.execute(
                "UPDATE runs SET state=?, updated_at=?, error_json=? WHERE run_id=?",
                (target.value, now, json.dumps(error), run_id),
            )
            c.execute(
                "INSERT INTO events(run_id, seq, at, state, category, detail) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, seq, now, target.value, category, json.dumps(error)),
            )

    # ------------------------------------------------------------------ #
    def _next_seq(self, run_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 AS s FROM events WHERE run_id=?",
                (run_id,)).fetchone()
        return int(row["s"])

    def get(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise StateConflictError("unknown run id", details={"run_id": run_id})
        return {
            "run_id": row["run_id"],
            "state": row["state"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "seed": row["seed"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
        }

    def events(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, at, state, category, detail FROM events "
                "WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()
        if not rows:
            raise StateConflictError("unknown run id", details={"run_id": run_id})
        return [{"seq": r["seq"], "at": r["at"], "state": r["state"],
                 "category": r["category"], "detail": json.loads(r["detail"])}
                for r in rows]

    def list_run_ids(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_id FROM runs ORDER BY created_at").fetchall()
        return [r[0] for r in rows]


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Key intermediate state retained for replay, without the full payload."""
    folds = result.get("fold_diagnostics", [])
    return {
        "n": result.get("n"),
        "folds": result.get("folds"),
        "fold_sizes": [f.get("valid_size") for f in folds],
        "point": result.get("point"),
        "se": result.get("se"),
        "ci": [result.get("ci_lower"), result.get("ci_upper")],
        "independent_units": result.get("independent_units"),
        "trimmed_fraction": result.get("trimmed_fraction"),
        "components": {
            "gcomp": result.get("gcomp_point"),
            "ipw": result.get("ipw_point"),
            "aipw": result.get("aipw_point"),
        },
    }
