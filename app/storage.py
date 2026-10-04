"""SQLite persistence: batches, contributions, aggregates.

Big integers (moduli, ciphertexts) are stored as decimal strings.
This module owns no business logic; it only reads and writes rows.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id        TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    state           TEXT NOT NULL,
    key_id          TEXT NOT NULL,
    public_n        TEXT NOT NULL,
    private_p       TEXT,
    private_q       TEXT,
    encoding_params TEXT NOT NULL,
    bound_used      TEXT NOT NULL DEFAULT '0',
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contributions (
    contribution_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id          TEXT NOT NULL REFERENCES batches(batch_id),
    participant_id    TEXT NOT NULL,
    key_id            TEXT NOT NULL,
    ciphertext        TEXT NOT NULL,
    coefficient       TEXT NOT NULL,
    plaintext_fixture TEXT,
    run_id            TEXT NOT NULL,
    submitted_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aggregates (
    batch_id            TEXT PRIMARY KEY REFERENCES batches(batch_id),
    result_ciphertext   TEXT NOT NULL,
    contribution_count  INTEGER NOT NULL,
    decrypted_plaintext TEXT,
    computed_at         TEXT NOT NULL,
    decrypted_at        TEXT,
    run_id              TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    batch_id   TEXT,
    event      TEXT NOT NULL,
    detail     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    def __init__(self, database_path: str):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(database_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- batches ---------------------------------------------------------
    def insert_batch(self, batch: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO batches
                   (batch_id, label, state, key_id, public_n, private_p,
                    private_q, encoding_params, bound_used, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    batch["batch_id"],
                    batch["label"],
                    batch["state"],
                    batch["key_id"],
                    batch["public_n"],
                    batch["private_p"],
                    batch["private_q"],
                    json.dumps(batch["encoding_params"]),
                    str(batch["bound_used"]),
                    _utcnow(),
                ),
            )

    def get_batch(self, batch_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
        return dict(row) if row else None

    def update_batch_state(self, batch_id: str, state: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE batches SET state = ? WHERE batch_id = ?",
                (state, batch_id),
            )

    def update_bound_used(self, batch_id: str, bound_used: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE batches SET bound_used = ? WHERE batch_id = ?",
                (str(bound_used), batch_id),
            )

    # --- contributions ---------------------------------------------------
    def insert_contribution(self, contribution: dict) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """INSERT INTO contributions
                   (batch_id, participant_id, key_id, ciphertext,
                    coefficient, plaintext_fixture, run_id, submitted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    contribution["batch_id"],
                    contribution["participant_id"],
                    contribution["key_id"],
                    str(contribution["ciphertext"]),
                    str(contribution["coefficient"]),
                    (
                        None
                        if contribution.get("plaintext_fixture") is None
                        else str(contribution["plaintext_fixture"])
                    ),
                    contribution["run_id"],
                    _utcnow(),
                ),
            )
            return int(cursor.lastrowid)

    def list_contributions(self, batch_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM contributions WHERE batch_id = ? "
                "ORDER BY contribution_id",
                (batch_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # --- aggregates ------------------------------------------------------
    def upsert_aggregate(self, aggregate: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO aggregates
                   (batch_id, result_ciphertext, contribution_count,
                    decrypted_plaintext, computed_at, decrypted_at, run_id)
                   VALUES (?, ?, ?, NULL, ?, NULL, ?)
                   ON CONFLICT(batch_id) DO UPDATE SET
                       result_ciphertext = excluded.result_ciphertext,
                       contribution_count = excluded.contribution_count,
                       decrypted_plaintext = NULL,
                       computed_at = excluded.computed_at,
                       decrypted_at = NULL,
                       run_id = excluded.run_id""",
                (
                    aggregate["batch_id"],
                    str(aggregate["result_ciphertext"]),
                    aggregate["contribution_count"],
                    _utcnow(),
                    aggregate["run_id"],
                ),
            )

    def mark_aggregate_decrypted(self, batch_id: str, plaintext: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE aggregates SET decrypted_plaintext = ?, decrypted_at = ? "
                "WHERE batch_id = ?",
                (str(plaintext), _utcnow(), batch_id),
            )

    def get_aggregate(self, batch_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM aggregates WHERE batch_id = ?", (batch_id,)
            ).fetchone()
        return dict(row) if row else None

    # --- audit -----------------------------------------------------------
    def append_audit(
        self, run_id: str, batch_id: str | None, event: str, detail: dict
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO audit_log (run_id, batch_id, event, detail, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (run_id, batch_id, event, json.dumps(detail, default=str), _utcnow()),
            )

    def list_audit(self, batch_id: str | None = None) -> list[dict]:
        with self._lock:
            if batch_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM audit_log ORDER BY audit_id"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM audit_log WHERE batch_id = ? ORDER BY audit_id",
                    (batch_id,),
                ).fetchall()
        return [dict(row) for row in rows]
