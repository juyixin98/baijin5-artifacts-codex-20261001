"""Evidence and diagnostics.

Every computed answer carries a request-scoped evidence record:

* ``request_id`` binds all log lines, DB rows and the JSON response together;
* ``steps`` records the key processing steps in order (design construction,
  enumeration vs Monte Carlo, inversion strategy, certification);
* ``failures`` lists structured failure codes with human explanations;
* ``uncertainties`` lists conclusions that are *not* exact (Monte Carlo
  error, non-certified inversion) so a caller never mistakes them for exact.

Failures and uncertainties are deliberately separate sections.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import __version__

_LOGGER_NAME = "pairtest"


def configure_logging(level: Optional[str] = None) -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [%(request_id)s] %(name)s@%(location)s: %(message)s",
                defaults={"request_id": "-", "location": "-"},
            )
        )
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel((level or os.environ.get("PAIRTEST_LOG_LEVEL", "INFO")).upper())
    return logger


@dataclass(frozen=False)
class Failure:
    code: str
    message: str
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=False)
class Step:
    seq: int
    name: str
    location: str
    summary: str
    data: Dict[str, Any] = field(default_factory=dict)


class EvidenceRecord:
    """Request-scoped collector; one instance per API request."""

    def __init__(self, request_id: Optional[str] = None):
        self.request_id = request_id or f"req_{uuid.uuid4().hex[:16]}"
        self.steps: List[Step] = []
        self.failures: List[Failure] = []
        self.uncertainties: List[str] = []
        self._logger = configure_logging()

    def step(self, name: str, summary: str, location: str, **data: Any) -> None:
        s = Step(len(self.steps) + 1, name, location, summary, dict(data))
        self.steps.append(s)
        extra = {"request_id": self.request_id, "location": location}
        self._logger.info("step %d %s: %s %s", s.seq, name, summary, data, extra=extra)

    def fail(self, code: str, message: str, location: str, **detail: Any) -> None:
        self.failures.append(Failure(code, message, dict(detail)))
        extra = {"request_id": self.request_id, "location": location}
        self._logger.warning("failure %s: %s %s", code, message, detail, extra=extra)

    def note_uncertainty(self, text: str) -> None:
        if text not in self.uncertainties:
            self.uncertainties.append(text)

    def envelope(self, result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Assemble the explainable response envelope."""
        return {
            "request_id": self.request_id,
            "service_version": __version__,
            "status": "ok" if not self.failures else "error",
            "result": result,
            "steps": [
                {
                    "seq": s.seq,
                    "name": s.name,
                    "location": s.location,
                    "summary": s.summary,
                    "detail": s.data,
                }
                for s in self.steps
            ],
            "failures": [
                {"code": f.code, "message": f.message, "detail": f.detail} for f in self.failures
            ],
            "uncertainties": list(self.uncertainties),
        }
