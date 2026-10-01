"""Structured logging helpers.

Every log line carries a run identity (``run_id``) and the package version so
that stored test logs can be tied back to the exact input batch and code
revision that produced a result.
"""

from __future__ import annotations

import logging
import platform
import sys
import uuid
from pathlib import Path
from typing import Any

from app import __version__

_FORMAT = "%(asctime)s | %(levelname)-7s | run=%(run_id)s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"

_configured = False


class _RunIdFilter(logging.Filter):
    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = getattr(record, "run_id", None) or self.run_id
        return True


def new_run_id(prefix: str = "run") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def configure_logging(log_dir: Path | str, level: str = "INFO", run_id: str | None = None) -> str:
    """Configure root logging once; return the run id attached to all lines."""
    global _configured
    run_id = run_id or new_run_id()
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "cuped.log"

    root = logging.getLogger()
    if not _configured:
        root.setLevel(logging.DEBUG)
        formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)
        run_filter = _RunIdFilter(run_id)

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(run_filter)

        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setLevel(getattr(logging, level, logging.INFO))
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(run_filter)

        root.addHandler(file_handler)
        root.addHandler(stream_handler)
        _configured = True

    logging.getLogger(__name__).info(
        "logging configured | version=%s python=%s platform=%s logfile=%s run_id=%s",
        __version__,
        platform.python_version(),
        platform.platform(),
        log_path,
        run_id,
    )
    return run_id


def get_logger(name: str, run_id: str | None = None) -> logging.LoggerAdapter:
    """Return an adapter that stamps every line with a run id."""
    return logging.LoggerAdapter(logging.getLogger(name), {"run_id": run_id or "-"})


def kv(mapping: dict[str, Any]) -> str:
    """Render a flat mapping as deterministic ``key=value`` pairs."""
    return " ".join(f"{k}={v}" for k, v in mapping.items())
