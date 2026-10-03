"""Diagnostics: structured, request-scoped, redacted logging.

Rules enforced here:

* every log line carries a ``request_id`` (and ``job_id`` when known);
* decisions (accepted / rejected / undecidable) are logged with their
  reason and failure category;
* pixel data, encoded image payloads, and raw profile bytes are **never**
  logged — only shapes, dtypes, and sha256 fingerprints.
"""
from __future__ import annotations

import hashlib
import logging
import uuid

LOGGER_NAME = "colorconvert"

#: Keys whose values must never be logged (defense in depth: the redactor
#: drops them even if a caller passes them by mistake).
SENSITIVE_KEYS = frozenset(
    {"image_b64", "alpha_b64", "image_bytes", "profile_bytes", "pixel_data"}
)


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def fingerprint(data: bytes) -> str:
    """Short, non-reversible identifier for binary payloads."""
    return hashlib.sha256(data).hexdigest()[:12]


def _redact(value):
    if isinstance(value, dict):
        return {
            k: ("<redacted>" if k in SENSITIVE_KEYS else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (bytes, bytearray)):
        return f"<bytes:{fingerprint(bytes(value))}>"
    if isinstance(value, (list, tuple)):
        return type(value)(_redact(v) for v in value)
    return value


def configure_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s "
                "[request_id=%(request_id)s] %(message)s %(fields)s"
            )
        )
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(level.upper())
    return logger


class RequestLogger:
    """Logger adapter binding a request_id and redacting sensitive fields."""

    def __init__(self, logger: logging.Logger, request_id: str) -> None:
        self._logger = logger
        self.request_id = request_id

    def _log(self, level: int, message: str, **fields) -> None:
        safe = _redact(fields)
        self._logger.log(
            level,
            message,
            extra={"request_id": self.request_id, "fields": safe},
        )

    def info(self, message: str, **fields) -> None:
        self._log(logging.INFO, message, **fields)

    def warning(self, message: str, **fields) -> None:
        self._log(logging.WARNING, message, **fields)

    def error(self, message: str, **fields) -> None:
        self._log(logging.ERROR, message, **fields)

    def decision(
        self,
        outcome: str,
        reason: str,
        *,
        category: str | None = None,
        job_id: str | None = None,
        **fields,
    ) -> None:
        level = logging.INFO if outcome == "accepted" else logging.WARNING
        self._log(
            level,
            f"decision={outcome} reason={reason}",
            category=category,
            job_id=job_id,
            **fields,
        )
