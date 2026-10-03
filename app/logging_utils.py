"""Structured run logging.

Every computational step emits one JSON line tagged with the run identity
(job id), the software versions, and a monotonic step number, so a test or
operator can correlate any log record back to the exact input/kernel digest
and the computation stage that produced it.
"""
from __future__ import annotations

import json
import logging
import platform
import sys
import time
from pathlib import Path

import numpy
import scipy

_VERSIONS = {
    "python": platform.python_version(),
    "numpy": numpy.__version__,
    "scipy": scipy.__version__,
}


def versions() -> dict[str, str]:
    return dict(_VERSIONS)


class RunLogger:
    """JSON-lines logger bound to one run of one job."""

    def __init__(self, run_id: str, log_dir: Path | None = None,
                 stream: bool = True):
        self.run_id = run_id
        self._step = 0
        self._handlers: list[logging.Handler] = []
        self._logger = logging.getLogger(f"tfs.run.{run_id}.{time.time_ns()}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        if stream:
            sh = logging.StreamHandler(sys.stderr)
            self._logger.addHandler(sh)
            self._handlers.append(sh)
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_dir / f"run-{run_id}.log", encoding="utf-8")
            self._logger.addHandler(fh)
            self._handlers.append(fh)

    def log(self, event: str, **fields: object) -> None:
        self._step += 1
        record = {
            "run_id": self.run_id,
            "step": self._step,
            "event": event,
            "versions": _VERSIONS,
            **fields,
        }
        self._logger.info(json.dumps(record, sort_keys=True, default=str))

    def close(self) -> None:
        for h in self._handlers:
            self._logger.removeHandler(h)
            h.close()
        self._handlers.clear()
