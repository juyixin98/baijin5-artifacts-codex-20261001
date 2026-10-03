"""Structured per-run logging.

Every processing run gets a ``run_id``; each log record is one JSON object
carrying the run id, a step name, library versions (on run start), input
hashes, progress counters and the final verdict inputs (measured peaks vs
threshold). Tests assert on these records so a failing run can always be
traced back to its input and environment.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import uuid

import numpy as np
import scipy

_default_logger = logging.getLogger("limiter.run")


def library_versions() -> dict:
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
    }


def pcm_sha256(pcm: np.ndarray) -> str:
    arr = np.ascontiguousarray(pcm, dtype=np.float64)
    return hashlib.sha256(arr.tobytes()).hexdigest()


class RunLog:
    """Emits structured JSON log records tied to one run id."""

    def __init__(self, logger: logging.Logger | None = None, run_id: str | None = None):
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self._logger = logger or _default_logger

    def event(self, step: str, **fields) -> dict:
        record = {"run_id": self.run_id, "step": step, **fields}
        self._logger.info("limiter %s", json.dumps(record, sort_keys=True))
        return record
