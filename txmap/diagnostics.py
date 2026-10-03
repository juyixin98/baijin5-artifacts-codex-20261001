"""Diagnostics: structured logging bound to a request id.

Log lines explain why a request was accepted, rejected, or indeterminate.
Sequence payloads are never logged raw — only lengths and hashes (see
provenance.redact).
"""

from __future__ import annotations

import logging

from .provenance import redact

_LOGGER_NAME = "txmap"


def get_logger() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s"
            )
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def log_decision(
    request_id: str,
    endpoint: str,
    status: str,
    reason: str,
    detail: dict,
) -> None:
    """Emit one structured decision line; `detail` is redacted first."""
    get_logger().info(
        "request_id=%s endpoint=%s decision=%s reason=%s detail=%s",
        request_id,
        endpoint,
        status,
        reason,
        redact(detail),
    )
