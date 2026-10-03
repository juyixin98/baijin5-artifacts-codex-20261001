"""Structured run logging.

Every computation run gets a ``run_id``; log records are JSON lines that
carry the run identity, component versions, input hash, the computation
step and the decision basis, so a log can be tied back to its input.
"""

from __future__ import annotations

import json
import logging
import platform
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import PIL
import scipy

from . import __version__

logger = logging.getLogger("seamcarve")


def component_versions() -> dict:
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "pillow": PIL.__version__,
        "seamcarve": __version__,
    }


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class RunLogger:
    """Collects structured records, mirrors them to the ``seamcarve``
    logger and optionally appends them to a JSON-lines file."""

    run_id: str = field(default_factory=new_run_id)
    log_path: Path | None = None
    records: list[dict] = field(default_factory=list)

    def emit(self, event: str, **fields) -> dict:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "event": event,
            **fields,
        }
        self.records.append(record)
        line = json.dumps(record, default=str, ensure_ascii=False)
        logger.info(line)
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return record

    def child(self, run_id: str) -> "RunLogger":
        return RunLogger(run_id=run_id, log_path=self.log_path)
