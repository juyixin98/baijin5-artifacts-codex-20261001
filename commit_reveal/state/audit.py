"""Append-only audit trail.

Every accepted or rejected protocol operation is recorded with the request
identifier, the round state-relevant reason, and a detail payload in which
secret material (random values, salts) is replaced by a short fingerprint.
The audit table is the primary diagnostic surface: given a request id you can
reconstruct why an operation was accepted, rejected, or left undetermined.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass

from commit_reveal.crypto.commitment import sha256_hex


def fingerprint(secret_hex: str) -> str:
    """Non-reversible, non-sensitive stand-in for a secret value in logs.

    Tolerates malformed (non-hex) input, since rejection paths fingerprint
    exactly the values that failed validation.
    """
    try:
        raw = bytes.fromhex(secret_hex)
    except ValueError:
        raw = secret_hex.encode("utf-8", errors="replace")
    return f"sha256:{sha256_hex(raw)[:12]}"


def mask_detail(detail: dict) -> dict:
    """Replace sensitive fields with fingerprints before persisting."""
    masked = {}
    for key, value in detail.items():
        if key in {"random_value", "salt"} and isinstance(value, str):
            masked[key] = fingerprint(value)
        else:
            masked[key] = value
    return masked


@dataclass(frozen=True)
class AuditEvent:
    ts: int
    request_id: str
    round_id: str | None
    event_type: str
    outcome: str  # ACCEPTED | REJECTED | UNDETERMINED
    reason: str
    detail: dict


class AuditLog:
    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock | None = None):
        self._conn = conn
        self._lock = lock or threading.RLock()

    def record(self, event: AuditEvent) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_events (ts, request_id, round_id, event_type,"
                " outcome, reason, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event.ts,
                    event.request_id,
                    event.round_id,
                    event.event_type,
                    event.outcome,
                    event.reason,
                    json.dumps(mask_detail(event.detail), sort_keys=True),
                ),
            )
            self._conn.commit()

    def list_for_round(self, round_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit_events WHERE round_id = ? ORDER BY id",
                (round_id,),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "ts": r["ts"],
                "request_id": r["request_id"],
                "round_id": r["round_id"],
                "event_type": r["event_type"],
                "outcome": r["outcome"],
                "reason": r["reason"],
                "detail": json.loads(r["detail"]),
            }
            for r in rows
        ]
