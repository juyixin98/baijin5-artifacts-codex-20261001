"""Request-scoped diagnostics with masked inputs.

Every accept / reject / undetermined decision is logged with a request id and
the key state that produced it. Raw feature values are never logged -- only
shape and a truncated content hash -- so diagnostics are safe to ship when
sequences carry sensitive data.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger("dtw_service")

# Decision statuses used across the service and the streaming layer.
STATUS_OK = "ok"
STATUS_UNREACHABLE = "unreachable"
STATUS_REJECTED = "rejected"
STATUS_UNDETERMINED = "undetermined"


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def mask_sequence(sequence: np.ndarray | list[list[float]]) -> dict[str, Any]:
    """Shape + truncated hash only; never the values themselves."""
    arr = np.asarray(sequence, dtype=np.float64)
    digest = hashlib.sha256(arr.tobytes()).hexdigest()[:12]
    shape = list(arr.shape) if arr.ndim > 0 else [0]
    return {"shape": shape, "sha256_12": digest}


@dataclass(frozen=True)
class DecisionRecord:
    request_id: str
    status: str
    reason: str
    state: dict[str, Any] = field(default_factory=dict)

    def to_log_fields(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status,
            "reason": self.reason,
            **self.state,
        }


def log_decision(record: DecisionRecord) -> None:
    log = logger.info if record.status == STATUS_OK else logger.warning
    log(
        "dtw decision status=%s request_id=%s reason=%s state=%s",
        record.status,
        record.request_id,
        record.reason,
        record.state,
        extra={"request_id": record.request_id},
    )
