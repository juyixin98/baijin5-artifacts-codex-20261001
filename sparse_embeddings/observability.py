"""Structured, run-correlated logging.

Every log line is one JSON object written to a JSONL file (and optionally
stderr). Lines always carry:

* ``run_id``  - caller-supplied or generated per request, so a result can be
  joined back to its exact inputs,
* versions   - package / numpy / python versions used for the computation,
* ``event`` / ``verdict`` - never an implicit success: rejections and unknown
  errors are logged with their :class:`ErrorCategory`,
* progress and decision basis - token count, touched rows, pre-clip norm,
  applied scales and which rule produced the verdict.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import threading
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from sparse_embeddings import __version__


def new_run_id() -> str:
    return uuid.uuid4().hex[:16]


def _json_default(obj: Any) -> Any:
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if is_dataclass(obj):
        return asdict(obj)
    return repr(obj)


class StructuredLogger:
    """Thread-safe JSONL append logger."""

    def __init__(self, path: Path | str | None = None, *, stderr: bool = True) -> None:
        self._path = Path(path) if path is not None else None
        self._stderr = stderr
        self._lock = threading.Lock()
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def event(
        self,
        event: str,
        *,
        run_id: str | None = None,
        verdict: str = "info",
        **fields: Any,
    ) -> dict[str, Any]:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "event": event,
            "verdict": verdict,
            "run_id": run_id or "-",
            "pid": os.getpid(),
            "versions": {
                "sparse_embeddings": __version__,
                "numpy": np.__version__,
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
            },
        }
        record.update(fields)
        line = json.dumps(record, default=_json_default, sort_keys=True)
        with self._lock:
            if self._path is not None:
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
            if self._stderr:
                sys.stderr.write(line + "\n")
        return record


class NullLogger(StructuredLogger):
    def __init__(self) -> None:  # noqa: D401 - no-op logger
        super().__init__(path=None, stderr=False)
