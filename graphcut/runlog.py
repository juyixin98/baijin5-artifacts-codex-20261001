"""Structured per-run logging.

Every segmentation run (sync endpoint or background job) gets a ``run_id``
and emits key=value log records for the important intermediate states
(input digest, graph size, big-M value, flow value, certificate gaps) so a
failing run can be replayed from the logs alone.
"""

from __future__ import annotations

import logging
import uuid

LOGGER_NAME = "graphcut"


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


class RunLogger:
    """Small helper that prefixes every record with the run id."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._log = get_logger()

    def state(self, event: str, **fields: object) -> None:
        kv = " ".join(f"{k}={v!r}" for k, v in sorted(fields.items()))
        self._log.info("run_id=%s event=%s %s", self.run_id, event, kv)

    def decision(self, event: str, reason: str, **fields: object) -> None:
        kv = " ".join(f"{k}={v!r}" for k, v in sorted(fields.items()))
        self._log.info("run_id=%s event=%s reason=%r %s", self.run_id, event, reason, kv)

    def failure(self, event: str, **fields: object) -> None:
        kv = " ".join(f"{k}={v!r}" for k, v in sorted(fields.items()))
        self._log.warning("run_id=%s event=%s %s", self.run_id, event, kv)
