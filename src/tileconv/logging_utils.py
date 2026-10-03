"""Structured run logging.

Every run (job execution, validation, memory check) gets a JSONL log file
whose lines carry the run identity, the relevant content digests, component
versions and the decision-relevant fields (tile progress, tolerances,
verdicts). Tests assert on these files to guarantee runs stay attributable.
"""

from __future__ import annotations

import json
import logging
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("tileconv")


def versions_snapshot() -> dict[str, str]:
    """Component versions recorded in every log and report."""
    snap: dict[str, str] = {"python": platform.python_version()}
    try:
        from . import __version__

        snap["tileconv"] = __version__
    except Exception:  # pragma: no cover
        snap["tileconv"] = "unknown"
    for mod, key in (("numpy", "numpy"), ("scipy", "scipy"), ("PIL", "pillow"),
                     ("fastapi", "fastapi")):
        try:
            m = __import__(mod)
            snap[key] = getattr(m, "__version__", "unknown")
        except Exception:
            snap[key] = "not-installed"
    return snap


class RunLogger:
    """Append-only JSONL logger bound to a run id."""

    def __init__(self, log_path: Path | str, run_id: str):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id

    def log(self, event: str, level: str = "info", **fields: Any) -> dict[str, Any]:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "event": event,
            "run_id": self.run_id,
            **fields,
        }
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        log_fn = getattr(logger, level if level in ("debug", "info", "warning", "error") else "info")
        log_fn("%s %s", event, json.dumps(fields, default=str))
        return record


def get_run_logger(logs_dir: Path | str, run_id: str) -> RunLogger:
    return RunLogger(Path(logs_dir) / f"run-{run_id}.jsonl", run_id)
