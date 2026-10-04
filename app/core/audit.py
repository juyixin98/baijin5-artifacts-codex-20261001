"""Structured diagnostics with redaction.

Audit events are written to both a logger and (via the service) the SQLite
audit table.  Sensitive values are never logged: callers pass explicit safe
state, and the redactor strips/hashes any field whose name hints at secret
material.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

SENSITIVE_NAME = re.compile(
    r"(key|token|secret|password|plaintext|ciphertext|payload|fragment)",
    re.IGNORECASE)

LOGGER_NAME = "ssea.audit"


def redact(name: str, value: Any) -> Any:
    """Return a log-safe representation of ``value``."""
    if value is None:
        return None
    if SENSITIVE_NAME.search(name):
        if isinstance(value, (bytes, bytearray)):
            digest = hashlib.sha256(bytes(value)).hexdigest()[:16]
            return f"<redacted:{len(value)}B:sha256:{digest}>"
        if isinstance(value, str):
            digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()
            return f"<redacted:{len(value)}c:sha256:{digest[:16]}>"
        return "<redacted>"
    if isinstance(value, (bytes, bytearray)):
        return f"<bytes:{len(value)}B>"
    return value


def safe_state(state: dict[str, Any] | None) -> dict[str, Any]:
    if not state:
        return {}
    return {k: redact(k, v) for k, v in state.items()}


class AuditLog:
    """Emit accept/reject/inconclusive events with request correlation ids."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger(LOGGER_NAME)

    def event(self, kind: str, category: str, request_id: str | None,
              message: str, **state: Any) -> dict[str, Any]:
        record = {
            "kind": kind,  # accept | reject | inconclusive | lifecycle
            "category": category,
            "request_id": request_id,
            "message": message,
            "state": safe_state(state),
        }
        level = {
            "accept": logging.INFO,
            "reject": logging.WARNING,
            "inconclusive": logging.WARNING,
            "lifecycle": logging.INFO,
        }.get(kind, logging.INFO)
        self._log.log(level, "%s", record)
        return record
