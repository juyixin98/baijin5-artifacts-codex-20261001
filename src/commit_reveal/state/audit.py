"""Audit trail with redaction.

Every accept/reject/undecidable decision is written to the SQLite audit log
*and* emitted as a structured JSON log line. Both carry the request id and
the key state (round, phase, decision, reason). Sensitive material — the
revealed value and salt — is never logged; only truncated SHA-256
fingerprints of participant ids and revealed material appear.
"""

from __future__ import annotations

import hashlib
import json
import logging

from commit_reveal.clock import Clock, to_iso
from commit_reveal.state.store import Store

logger = logging.getLogger("commit_reveal.audit")


def tag(text: str) -> str:
    """Short, non-reversible tag for correlating a participant in logs."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def fingerprint(hex_material: str) -> str:
    """Redacted fingerprint of sensitive hex material (value/salt)."""
    return hashlib.sha256(bytes.fromhex(hex_material)).hexdigest()[:12]


class Audit:
    def __init__(self, store: Store, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def record(
        self,
        request_id: str,
        event: str,
        decision: str,
        reason: str,
        round_id: str | None = None,
        participant_id: str | None = None,
        detail: dict | None = None,
    ) -> None:
        participant_tag = tag(participant_id) if participant_id else None
        detail_json = json.dumps(detail, sort_keys=True) if detail else None
        self._store.append_audit(
            ts=to_iso(self._clock.now()),
            request_id=request_id,
            event=event,
            round_id=round_id,
            participant_tag=participant_tag,
            decision=decision,
            reason=reason,
            detail=detail_json,
        )
        logger.info(
            json.dumps(
                {
                    "request_id": request_id,
                    "event": event,
                    "round_id": round_id,
                    "participant_tag": participant_tag,
                    "decision": decision,
                    "reason": reason,
                    "detail": detail,
                },
                sort_keys=True,
            )
        )
