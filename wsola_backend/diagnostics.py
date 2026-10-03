"""Diagnostics: request ids, decision records, masked logging.

Every accept / reject / undecidable decision is recorded as a
DiagnosticRecord with the request id and the key state that explains it.
Raw audio is sensitive business data in deployments: logs only ever see
the sample COUNT and a truncated SHA-256 fingerprint, never sample values.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger("wsola")


def new_request_id() -> str:
    return uuid.uuid4().hex


def content_fingerprint(samples: np.ndarray) -> str:
    """Truncated content hash: identifies identical inputs without data."""
    contiguous = np.ascontiguousarray(samples, dtype=np.float64)
    return hashlib.sha256(contiguous.tobytes()).hexdigest()[:12]


@dataclass
class Diagnostic:
    level: str  # "info" | "warning"
    code: str
    message: str
    context: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "context": dict(self.context),
        }


@dataclass
class DecisionLog:
    """Collects the decisions taken while serving one request."""

    request_id: str
    records: list[Diagnostic] = field(default_factory=list)

    def _add(self, level: str, code: str, message: str, **context) -> Diagnostic:
        record = Diagnostic(level=level, code=code, message=message, context=context)
        self.records.append(record)
        log = logger.warning if level == "warning" else logger.info
        log(
            "%s %s: %s | %s",
            self.request_id,
            code,
            message,
            {k: v for k, v in context.items()},
        )
        return record

    def info(self, code: str, message: str, **context) -> Diagnostic:
        return self._add("info", code, message, **context)

    def warning(self, code: str, message: str, **context) -> Diagnostic:
        return self._add("warning", code, message, **context)
