"""SQLite-backed state and audit boundary.

This layer persists key *metadata* and audit events. Key material (the raw
root, intermediate tree keys, derived outputs) never crosses this boundary:
the registry stores identity fields and display names; the audit log stores
run ids, key ids, request digests, outcomes and machine-readable reasons.

Error contract: constraint violations that reflect a clash with persisted
state raise :class:`StateConflictError`; quota breaches raise
:class:`ResourceExhaustedError`.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid

from .errors import ResourceExhaustedError, StateConflictError
from .identity import KeyIdentity

_SCHEMA = """
CREATE TABLE IF NOT EXISTS keys (
    key_id       TEXT PRIMARY KEY,
    tenant       TEXT NOT NULL,
    purpose      TEXT NOT NULL,
    version      INTEGER NOT NULL,
    context      TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at   REAL NOT NULL,
    run_id       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_keys_tenant ON keys (tenant);

CREATE TABLE IF NOT EXISTS audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    ts           REAL NOT NULL,
    event        TEXT NOT NULL,
    key_id       TEXT,
    request_hash TEXT,
    outcome      TEXT NOT NULL,
    reason       TEXT
);
"""


def new_run_id() -> str:
    return uuid.uuid4().hex[:16]


class StateStore:
    def __init__(self, db_path: str = ":memory:", *, max_keys_per_tenant: int = 1000) -> None:
        self._max_keys_per_tenant = max_keys_per_tenant
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- key registry -----------------------------------------------------

    def register_key(
        self, identity: KeyIdentity, display_name: str, *, run_id: str
    ) -> bool:
        """Register ``identity`` under ``display_name``.

        Returns True when a new row was created, False when the key id was
        already registered with the same display name (idempotent replay).
        Raises StateConflictError when the key id exists under a *different*
        display name, and ResourceExhaustedError when the tenant quota is
        exhausted.
        """
        key_id = identity.key_id
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT display_name FROM keys WHERE key_id = ?", (key_id,)
            ).fetchone()
            if row is not None:
                if row["display_name"] == display_name:
                    return False
                raise StateConflictError(
                    "key id already registered under a different display name",
                    detail=f"key_id={key_id}",
                )
            count = self._conn.execute(
                "SELECT COUNT(*) AS n FROM keys WHERE tenant = ?", (identity.tenant,)
            ).fetchone()["n"]
            if count >= self._max_keys_per_tenant:
                raise ResourceExhaustedError(
                    "per-tenant key quota exhausted",
                    detail=f"tenant={identity.tenant} quota={self._max_keys_per_tenant}",
                )
            self._conn.execute(
                "INSERT INTO keys (key_id, tenant, purpose, version, context,"
                " display_name, created_at, run_id) VALUES (?,?,?,?,?,?,?,?)",
                (
                    key_id,
                    identity.tenant,
                    identity.purpose,
                    identity.version,
                    identity.context,
                    display_name,
                    time.time(),
                    run_id,
                ),
            )
            return True

    def get_key(self, key_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT key_id, tenant, purpose, version, context, display_name,"
                " created_at, run_id FROM keys WHERE key_id = ?",
                (key_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    # -- audit log ----------------------------------------------------------

    def record_audit(
        self,
        *,
        run_id: str,
        event: str,
        outcome: str,
        key_id: str | None = None,
        request_hash: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Append an audit entry. Only digests and identifiers — never keys."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO audit (run_id, ts, event, key_id, request_hash,"
                " outcome, reason) VALUES (?,?,?,?,?,?,?)",
                (run_id, time.time(), event, key_id, request_hash, outcome, reason),
            )

    def list_audit(self, *, run_id: str | None = None) -> list[dict]:
        with self._lock:
            if run_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM audit ORDER BY id"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM audit WHERE run_id = ? ORDER BY id", (run_id,)
                ).fetchall()
        return [dict(r) for r in rows]
