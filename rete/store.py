"""SQLite evidence store.

Persists an auditable trail for every run:
  * runs          — one row per bounded run, with explicit final status;
  * fact_events   — every insert/retract (kind, fields, wme id, refcount);
  * activations   — every firing with fact provenance and action outcomes;
  * rule_defs     — the submitted rule bodies (JSON).

All rows are correlated by session_id / run_id so a log can always be tied
back to the input and execution it came from.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    max_cycles      INTEGER NOT NULL,
    cycles          INTEGER,
    status          TEXT,
    engine_version  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fact_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    run_id      TEXT,
    ts          TEXT NOT NULL,
    op          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    fields      TEXT NOT NULL,
    wme_id      INTEGER NOT NULL,
    refcount    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS activations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    cycle           INTEGER NOT NULL,
    rule            TEXT NOT NULL,
    salience        INTEGER NOT NULL,
    fact_ids        TEXT NOT NULL,
    fact_keys       TEXT NOT NULL,
    bindings        TEXT NOT NULL,
    action_results  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rule_defs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    rule_name   TEXT NOT NULL,
    body        TEXT NOT NULL,
    added_at    TEXT NOT NULL,
    UNIQUE(session_id, rule_name)
);
"""


class EvidenceStore:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        # check_same_thread=False + an explicit write lock: the FastAPI
        # service may touch a session from different worker threads.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- writes --------------------------------------------------------------
    def record_rule(self, session_id: str, rule_name: str, body: dict,
                    added_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO rule_defs "
                "(session_id, rule_name, body, added_at) VALUES (?, ?, ?, ?)",
                (session_id, rule_name, json.dumps(body, sort_keys=True),
                 added_at))
            self._conn.commit()

    def start_run(self, run_id: str, started_at: str, max_cycles: int,
                  engine_version: str, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs (run_id, session_id, started_at, "
                "max_cycles, engine_version) VALUES (?, ?, ?, ?, ?)",
                (run_id, session_id, started_at, max_cycles, engine_version))
            self._conn.commit()

    def finish_run(self, run_id: str, finished_at: str, status: str,
                   cycles: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET finished_at = ?, status = ?, cycles = ? "
                "WHERE run_id = ?", (finished_at, status, cycles, run_id))
            self._conn.commit()

    def record_fact_event(self, *, session_id: str, run_id: str | None,
                          ts: str, op: str, kind: str, fields: list,
                          wme_id: int, refcount: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO fact_events (session_id, run_id, ts, op, kind, "
                "fields, wme_id, refcount) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, run_id, ts, op, kind,
                 json.dumps(fields, sort_keys=True), wme_id, refcount))
            self._conn.commit()

    def record_activation(self, run_id: str, rec: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO activations (run_id, cycle, rule, salience, "
                "fact_ids, fact_keys, bindings, action_results) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, rec["cycle"], rec["rule"], rec["salience"],
                 json.dumps(rec["fact_ids"]),
                 json.dumps(rec["fact_keys"], sort_keys=True),
                 json.dumps(rec["bindings"], sort_keys=True),
                 json.dumps(rec["action_results"], sort_keys=True)))
            self._conn.commit()

    # -- reads ---------------------------------------------------------------
    def _query(self, sql: str, params: tuple) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def list_runs(self, session_id: str) -> list[dict]:
        return self._query(
            "SELECT * FROM runs WHERE session_id = ? ORDER BY started_at",
            (session_id,))

    def get_run(self, run_id: str) -> dict | None:
        rows = self._query("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        return rows[0] if rows else None

    def list_activations(self, run_id: str) -> list[dict]:
        rows = self._query(
            "SELECT * FROM activations WHERE run_id = ? ORDER BY cycle",
            (run_id,))
        for r in rows:
            for col in ("fact_ids", "fact_keys", "bindings", "action_results"):
                r[col] = json.loads(r[col])
        return rows

    def list_fact_events(self, session_id: str,
                         run_id: str | None = None) -> list[dict]:
        if run_id is not None:
            rows = self._query(
                "SELECT * FROM fact_events WHERE session_id = ? AND run_id = ?"
                " ORDER BY id", (session_id, run_id))
        else:
            rows = self._query(
                "SELECT * FROM fact_events WHERE session_id = ? ORDER BY id",
                (session_id,))
        for r in rows:
            r["fields"] = json.loads(r["fields"])
        return rows
