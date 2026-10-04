"""SQLite-backed state and audit storage.

Three kinds of state, with explicit contracts:

- meta:     singleton rows (the local test root key and its fingerprint).
- registry: one row per (key_id, length) derivation result with the
            canonical descriptor, an HMAC fingerprint of the key material,
            and an optional display name. Re-deriving the same identity at
            the same length with a matching fingerprint is an idempotent
            no-op; a mismatching fingerprint for an existing row is a
            state_conflict (the store disagrees with fresh computation).
            Registry rows never contain key material.
- names:    display_name -> key_id bindings. A display name may bind to at
            most one key_id; rebinding to a different key_id is a
            state_conflict. Lookups by name never influence derivation.
- audit:    append-only operation log (run_id, event, outcome, category).

Storage failures are wrapped as computation_failure so upper layers see
only the service error taxonomy. FastAPI serves endpoints from worker
threads, so the connection is created with check_same_thread=False and
every access is serialized through one lock.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .errors import ComputationError, StateConflictError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS registry (
    key_id         TEXT NOT NULL,
    length         INTEGER NOT NULL,
    descriptor_hex TEXT NOT NULL,
    display_name   TEXT,
    fingerprint    TEXT NOT NULL,
    created_run    TEXT NOT NULL,
    created_at     REAL NOT NULL,
    PRIMARY KEY (key_id, length)
);
CREATE TABLE IF NOT EXISTS names (
    display_name TEXT PRIMARY KEY,
    key_id       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL,
    ts             REAL NOT NULL,
    event          TEXT NOT NULL,
    key_id         TEXT,
    outcome        TEXT NOT NULL,
    error_category TEXT,
    detail_json    TEXT
);
"""


class Store:
    """Thin thread-safe synchronous wrapper over a SQLite database file."""

    def __init__(self, path: str | Path):
        self._lock = threading.Lock()
        try:
            self._conn = sqlite3.connect(str(path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            with self._lock:
                self._conn.executescript(_SCHEMA)
                self._conn.commit()
        except sqlite3.Error as exc:
            raise ComputationError(
                "failed to open state database",
                details={"error": type(exc).__name__},
            ) from exc

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- meta -------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT value FROM meta WHERE key = ?", (key,)
                ).fetchone()
        except sqlite3.Error as exc:
            raise ComputationError(
                "meta read failed", details={"error": type(exc).__name__}
            ) from exc
        return None if row is None else str(row["value"])

    def set_meta_if_absent(self, key: str, value: str) -> bool:
        """Insert a meta row only if missing. Returns True if inserted."""
        try:
            with self._lock:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
                    (key, value),
                )
                self._conn.commit()
        except sqlite3.Error as exc:
            raise ComputationError(
                "meta write failed", details={"error": type(exc).__name__}
            ) from exc
        return cur.rowcount == 1

    # -- registry -----------------------------------------------------------

    def get_registered(
        self, key_id: str, length: int | None = None
    ) -> dict[str, Any] | None:
        """Fetch one registry row. With length=None, any row for the key_id
        (metadata lookups); with a length, the exact (key_id, length) row."""
        try:
            with self._lock:
                if length is None:
                    row = self._conn.execute(
                        "SELECT * FROM registry WHERE key_id = ? LIMIT 1",
                        (key_id,),
                    ).fetchone()
                else:
                    row = self._conn.execute(
                        "SELECT * FROM registry WHERE key_id = ? AND length = ?",
                        (key_id, length),
                    ).fetchone()
        except sqlite3.Error as exc:
            raise ComputationError(
                "registry read failed", details={"error": type(exc).__name__}
            ) from exc
        return None if row is None else dict(row)

    def register_key(
        self,
        *,
        key_id: str,
        descriptor_hex: str,
        display_name: str | None,
        fingerprint: str,
        length: int,
        run_id: str,
    ) -> None:
        """Register a derived key. A mismatching fingerprint for an existing
        (key_id, length) row is a state_conflict; an identical row is a
        no-op (deterministic re-derivation)."""
        existing = self.get_registered(key_id, length)
        if existing is not None:
            if existing["fingerprint"] != fingerprint:
                raise StateConflictError(
                    "registry disagrees with fresh derivation",
                    details={"key_id": key_id, "length": length},
                )
            return
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO registry(key_id, length, descriptor_hex,"
                    " display_name, fingerprint, created_run, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        key_id,
                        length,
                        descriptor_hex,
                        display_name,
                        fingerprint,
                        run_id,
                        time.time(),
                    ),
                )
                self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise StateConflictError(
                "registry insert conflict",
                details={"key_id": key_id, "error": str(exc)},
            ) from exc
        except sqlite3.Error as exc:
            raise ComputationError(
                "registry write failed", details={"error": type(exc).__name__}
            ) from exc

    # -- display-name bindings ----------------------------------------------

    def bind_name(self, display_name: str, key_id: str) -> None:
        if self.get_registered(key_id) is None:
            raise StateConflictError(
                "cannot bind a name to an unregistered key",
                details={"key_id": key_id},
            )
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT key_id FROM names WHERE display_name = ?",
                    (display_name,),
                ).fetchone()
                if row is not None and row["key_id"] != key_id:
                    raise StateConflictError(
                        "display name already bound to a different key",
                        details={"display_name": display_name},
                    )
                self._conn.execute(
                    "INSERT OR IGNORE INTO names(display_name, key_id)"
                    " VALUES (?, ?)",
                    (display_name, key_id),
                )
                self._conn.commit()
        except sqlite3.Error as exc:
            raise ComputationError(
                "name binding failed", details={"error": type(exc).__name__}
            ) from exc

    # -- audit ----------------------------------------------------------------

    def audit(
        self,
        *,
        run_id: str,
        event: str,
        outcome: str,
        key_id: str | None = None,
        error_category: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO audit(run_id, ts, event, key_id, outcome,"
                    " error_category, detail_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        time.time(),
                        event,
                        key_id,
                        outcome,
                        error_category,
                        json.dumps(detail or {}, sort_keys=True),
                    ),
                )
                self._conn.commit()
        except sqlite3.Error as exc:
            raise ComputationError(
                "audit write failed", details={"error": type(exc).__name__}
            ) from exc

    def audit_for_run(self, run_id: str) -> list[dict[str, Any]]:
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT * FROM audit WHERE run_id = ? ORDER BY id", (run_id,)
                ).fetchall()
        except sqlite3.Error as exc:
            raise ComputationError(
                "audit read failed", details={"error": type(exc).__name__}
            ) from exc
        return [dict(r) for r in rows]
