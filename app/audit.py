"""Audit trail: every state-changing operation is recorded with a run id.

Each API operation carries a run_id so test logs and database rows can be
correlated back to the request (and to the pytest run) that produced them.
Audit entries are also mirrored to the `paillier.audit` logger.
"""

from __future__ import annotations

import logging

from .storage import Storage

logger = logging.getLogger("paillier.audit")


class AuditLogger:
    def __init__(self, storage: Storage):
        self._storage = storage

    def record(
        self,
        run_id: str,
        event: str,
        batch_id: str | None = None,
        **detail,
    ) -> None:
        self._storage.append_audit(run_id, batch_id, event, detail)
        logger.info(
            "run_id=%s batch_id=%s event=%s detail=%s",
            run_id,
            batch_id,
            event,
            detail,
        )
