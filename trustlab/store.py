"""SQLite-backed state and audit store (the state & audit boundary).

Persists: trust-bundle versions, connection registry, audit events and
handshake-failure records. Every row carries the service ``run_id`` so a
full run can be replayed from the database alone.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading

UTC = dt.timezone.utc


def utcnow() -> str:
    return dt.datetime.now(UTC).isoformat(timespec="milliseconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS bundles (
    version      INTEGER PRIMARY KEY,
    roots_json   TEXT NOT NULL,
    status       TEXT NOT NULL,          -- active | retired
    kind         TEXT NOT NULL,          -- initial | manual | rotation_overlap
                                         -- | rotation_final | rollback
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    activated_at TEXT NOT NULL,
    retired_at   TEXT
);
CREATE TABLE IF NOT EXISTS connections (
    id             TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL,
    identity_json  TEXT NOT NULL,
    bundle_version INTEGER NOT NULL,
    peer           TEXT NOT NULL,
    established_at TEXT NOT NULL,
    closed_at      TEXT,
    close_reason   TEXT,
    revoked        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    ts         TEXT NOT NULL,
    category   TEXT NOT NULL,
    event      TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    reasoning  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS handshake_failures (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                TEXT NOT NULL,
    ts                    TEXT NOT NULL,
    failure_class         TEXT NOT NULL,
    layer                 TEXT NOT NULL,  -- tls | application
    verify_code           INTEGER,
    verify_message        TEXT,
    active_bundle_version INTEGER,
    peer                  TEXT,
    explanation_json      TEXT
);
"""


class Store:
    def __init__(self, path) -> None:
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- bundles ----------------------------------------------------------
    def insert_bundle(self, *, version: int, roots: list[str], status: str,
                      kind: str, note: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO bundles(version, roots_json, status, kind, note,"
                " created_at, activated_at) VALUES (?,?,?,?,?,?,?)",
                (version, json.dumps(roots), status, kind, note,
                 utcnow(), utcnow()),
            )

    def set_bundle_status(self, version: int, status: str) -> None:
        retired_at = utcnow() if status == "retired" else None
        with self._lock, self._db:
            self._db.execute(
                "UPDATE bundles SET status=?, retired_at=COALESCE(?, retired_at)"
                " WHERE version=?",
                (status, retired_at, version),
            )

    def get_bundle(self, version: int) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM bundles WHERE version=?", (version,)
            ).fetchone()
        return self._bundle_row(row) if row else None

    def list_bundles(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM bundles ORDER BY version"
            ).fetchall()
        return [self._bundle_row(r) for r in rows]

    def max_bundle_version(self) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(MAX(version), 0) AS v FROM bundles"
            ).fetchone()
        return int(row["v"])

    def active_bundle(self) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM bundles WHERE status='active'"
                " ORDER BY version DESC LIMIT 1"
            ).fetchone()
        return self._bundle_row(row) if row else None

    @staticmethod
    def _bundle_row(row: sqlite3.Row) -> dict:
        return {
            "version": row["version"],
            "roots": json.loads(row["roots_json"]),
            "status": row["status"],
            "kind": row["kind"],
            "note": row["note"],
            "created_at": row["created_at"],
            "activated_at": row["activated_at"],
            "retired_at": row["retired_at"],
        }

    # -- connections ------------------------------------------------------
    def add_connection(self, *, conn_id: str, run_id: str, identity: dict,
                       bundle_version: int, peer: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO connections(id, run_id, identity_json,"
                " bundle_version, peer, established_at)"
                " VALUES (?,?,?,?,?,?)",
                (conn_id, run_id, json.dumps(identity), bundle_version,
                 peer, utcnow()),
            )

    def close_connection(self, conn_id: str, reason: str) -> None:
        # First writer wins: an administrative reason recorded before the
        # socket teardown must not be overwritten by the handler's finally.
        with self._lock, self._db:
            self._db.execute(
                "UPDATE connections SET closed_at=?, close_reason=?"
                " WHERE id=? AND closed_at IS NULL",
                (utcnow(), reason, conn_id),
            )

    def mark_revoked(self, conn_id: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE connections SET revoked=1 WHERE id=?", (conn_id,)
            )

    def get_connection(self, conn_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM connections WHERE id=?", (conn_id,)
            ).fetchone()
        return self._conn_row(row) if row else None

    def list_connections(self, run_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM connections"
        args: tuple = ()
        if run_id:
            sql += " WHERE run_id=?"
            args = (run_id,)
        sql += " ORDER BY established_at, id"
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [self._conn_row(r) for r in rows]

    @staticmethod
    def _conn_row(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "identity": json.loads(row["identity_json"]),
            "bundle_version": row["bundle_version"],
            "peer": row["peer"],
            "established_at": row["established_at"],
            "closed_at": row["closed_at"],
            "close_reason": row["close_reason"],
            "revoked": bool(row["revoked"]),
        }

    # -- audit ------------------------------------------------------------
    def audit(self, *, run_id: str, category: str, event: str,
              detail: dict | None = None, reasoning: str = "") -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO audit(run_id, ts, category, event, detail_json,"
                " reasoning) VALUES (?,?,?,?,?,?)",
                (run_id, utcnow(), category, event,
                 json.dumps(detail or {}), reasoning),
            )

    def list_audit(self, *, run_id: str | None = None,
                   category: str | None = None) -> list[dict]:
        sql = "SELECT * FROM audit WHERE 1=1"
        args: list = []
        if run_id:
            sql += " AND run_id=?"
            args.append(run_id)
        if category:
            sql += " AND category=?"
            args.append(category)
        sql += " ORDER BY id"
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [
            {
                "id": r["id"],
                "run_id": r["run_id"],
                "ts": r["ts"],
                "category": r["category"],
                "event": r["event"],
                "detail": json.loads(r["detail_json"]),
                "reasoning": r["reasoning"],
            }
            for r in rows
        ]

    # -- handshake failures -------------------------------------------------
    def add_handshake_failure(self, *, run_id: str, failure_class: str,
                              layer: str, verify_code: int | None,
                              verify_message: str | None,
                              active_bundle_version: int | None,
                              peer: str | None,
                              explanation: dict | None) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO handshake_failures(run_id, ts, failure_class,"
                " layer, verify_code, verify_message, active_bundle_version,"
                " peer, explanation_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, utcnow(), failure_class, layer, verify_code,
                 verify_message, active_bundle_version, peer,
                 json.dumps(explanation) if explanation else None),
            )

    def list_handshake_failures(self, run_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM handshake_failures"
        args: tuple = ()
        if run_id:
            sql += " WHERE run_id=?"
            args = (run_id,)
        sql += " ORDER BY id"
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [
            {
                "id": r["id"],
                "run_id": r["run_id"],
                "ts": r["ts"],
                "failure_class": r["failure_class"],
                "layer": r["layer"],
                "verify_code": r["verify_code"],
                "verify_message": r["verify_message"],
                "active_bundle_version": r["active_bundle_version"],
                "peer": r["peer"],
                "explanation": (json.loads(r["explanation_json"])
                                if r["explanation_json"] else None),
            }
            for r in rows
        ]
