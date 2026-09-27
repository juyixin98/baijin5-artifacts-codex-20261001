"""Structured diagnostics.

Every accept / reject / undecidable decision in the system is recorded as a
``Diagnostic`` carrying a request (or record) id, the component that decided,
and the key state that explains the decision.

Sensitive-data policy: tensor *values* are never logged. ``tensor_state``
exposes only name, shape, dtype, requires_grad and the version counter, which
are sufficient to explain a decision without leaking payload data.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field

from .config import get_settings

STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_UNDECIDABLE = "undecidable"


@dataclass
class Diagnostic:
    request_id: str
    component: str
    status: str  # accepted | rejected | undecidable
    reason: str
    state: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


def tensor_state(tensor) -> dict:
    """Masked view of a tensor for logs: metadata only, never raw values."""
    return {
        "name": tensor.name,
        "shape": list(tensor.shape),
        "dtype": str(tensor.data.dtype),
        "requires_grad": tensor.requires_grad,
        "version": tensor._version,
    }


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        diagnostic = getattr(record, "diagnostic", None)
        if diagnostic is not None:
            payload["diagnostic"] = diagnostic.to_dict()
        return json.dumps(payload, default=str)


def get_logger(name: str = "minigrad") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(get_settings().log_level)
        logger.propagate = False
    return logger


def log_diagnostic(diagnostic: Diagnostic) -> None:
    logger = get_logger()
    level = logging.INFO if diagnostic.status == STATUS_ACCEPTED else logging.WARNING
    logger.log(level, "diagnostic", extra={"diagnostic": diagnostic})
