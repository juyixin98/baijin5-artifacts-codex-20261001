"""Per-request diagnostics with redaction.

Every accepted / rejected / undetermined decision is recorded as a
:class:`DiagnosticEvent` carrying the request id and a small state dict.
State dicts must only contain redacted facts (digests, sizes, enum
names) - never pixel data, profile bytes or base64 payloads.  The same
events are returned in API responses and emitted to the ``iccconv``
logger, so logs are safe to ship by construction.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("iccconv")


@dataclass(frozen=True)
class DiagnosticEvent:
    step: str
    outcome: str  # accepted | rejected | undetermined | info
    message: str
    state: dict[str, Any] = field(default_factory=dict)


class Diagnostics:
    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self._events: list[DiagnosticEvent] = []

    def record(self, step: str, outcome: str, message: str, **state: Any) -> None:
        event = DiagnosticEvent(step=step, outcome=outcome, message=message, state=state)
        self._events.append(event)
        logger.info(
            json.dumps(
                {
                    "request_id": self.request_id,
                    "step": step,
                    "outcome": outcome,
                    "message": message,
                    "state": state,
                },
                default=str,
            )
        )

    def as_list(self) -> list[dict[str, Any]]:
        return [
            {"step": e.step, "outcome": e.outcome, "message": e.message, "state": e.state}
            for e in self._events
        ]


def new_request_id() -> str:
    import uuid

    return uuid.uuid4().hex


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)
