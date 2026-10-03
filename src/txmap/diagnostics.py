"""Structured diagnostics with redaction.

Diagnostic records carry a request/audit identifier and the key state that
explains why a request was accepted, rejected, or could not be decided. Any
field whose name looks sensitive is masked; long base strings are truncated.
"""

from __future__ import annotations

import json
import logging
from typing import Any

SENSITIVE_KEY_PARTS = ("token", "secret", "password", "api_key", "apikey", "authorization")
MAX_VALUE_LEN = 80

logger = logging.getLogger("txmap")


def redact(payload: Any) -> Any:
    """Return a log-safe copy of *payload*."""
    if isinstance(payload, dict):
        out = {}
        for key, value in payload.items():
            lk = str(key).lower()
            if any(part in lk for part in SENSITIVE_KEY_PARTS):
                out[key] = "***REDACTED***"
            else:
                out[key] = redact(value)
        return out
    if isinstance(payload, (list, tuple)):
        return [redact(v) for v in payload]
    if isinstance(payload, str) and len(payload) > MAX_VALUE_LEN:
        return payload[:20] + f"...<{len(payload)} chars>"
    return payload


def configure_logging(level: str = "INFO") -> None:
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s txmap [%(request_id)s] %(event)s :: %(detail)s"
            )
        )
        logger.addHandler(handler)
    logger.setLevel(level)


def log_event(level: int, event: str, request_id: str, **state: Any) -> None:
    safe = redact(state)
    logger.log(
        level,
        "",
        extra={"request_id": request_id, "event": event,
               "detail": json.dumps(safe, sort_keys=True, ensure_ascii=False)},
    )
