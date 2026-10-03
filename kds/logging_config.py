"""Diagnostic logging boundary.

Logs carry run ids, key ids, request digests and error categories so a
failure can be replayed. Key material — the raw root, intermediate tree
keys and derived outputs — is never passed to the logger; the service only
logs identifiers and digests. ``assert_clean_of`` is a test/verification
helper that scans captured log text for forbidden byte strings.
"""

from __future__ import annotations

import logging

_FORMAT = "%(asctime)s %(levelname)s %(name)s run=%(run_id)s %(message)s"


class _RunIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "run_id"):
            record.run_id = "-"
        return True


def get_logger(name: str = "kds") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    for handler in logger.handlers:
        if not any(isinstance(f, _RunIdFilter) for f in handler.filters):
            handler.addFilter(_RunIdFilter())
    return logger


def log_with_run(logger: logging.Logger, level: int, run_id: str, msg: str, *args) -> None:
    logger.log(level, msg, *args, extra={"run_id": run_id})


def assert_clean_of(log_text: str, *forbidden: bytes) -> None:
    """Raise AssertionError if any forbidden byte string appears in the logs."""
    for blob in forbidden:
        for needle in {blob.hex(), blob.decode("utf-8", errors="ignore")}:
            if needle and needle in log_text:
                raise AssertionError(f"sensitive material leaked into logs: {needle!r}")
