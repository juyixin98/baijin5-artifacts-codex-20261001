"""Audit log.

Every accept / reject / undecidable decision is appended here with the
request id, the message id, the decision and the reason.  Only
non-sensitive metadata (ids, sequence numbers, lengths, SHA-256 hashes)
may be recorded - plaintext, keys, nonces and ciphertext never enter the
audit trail.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid a store <-> audit import cycle
    from .store import Store

# Decisions
ACCEPT = "accept"
REJECT = "reject"
UNDECIDABLE = "undecidable"


def content_hash(data: bytes) -> str:
    """SHA-256 hex digest - the only representation of content allowed in logs."""
    return hashlib.sha256(data).hexdigest()


class AuditLog:
    def __init__(self, store: "Store") -> None:
        self._store = store

    def record(
        self,
        *,
        request_id: str,
        event: str,
        decision: str,
        reason: str,
        message_id: str | None = None,
        detail: dict | None = None,
    ) -> None:
        self._store.insert_audit(
            ts=time.time(),
            request_id=request_id,
            message_id=message_id,
            event=event,
            decision=decision,
            reason=reason,
            detail_json=json.dumps(detail or {}, sort_keys=True),
        )

    def for_message(self, message_id: str) -> list[dict]:
        return self._store.list_audit(message_id=message_id)
