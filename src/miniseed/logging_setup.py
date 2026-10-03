"""Result provenance logging.

Every meaningful run emits structured, correlation-capable log lines::

    2026-10-03T.. INFO [miniseed] request_id=ab12 run_id=r1 step=scan \
        verdict=OK k=9 w=5 hash_version=fnv1a-2bit-v1 windows=.. seeds=..

Contract:

* each line carries the run identity and/or a request id so a log line can be
  tied back to its input/invocation;
* service startup logs the relevant versions (Python/NumPy/hash version);
* a failure logs ``verdict=ERROR`` with the error code -- exceptions are never
  logged as success.
"""
from __future__ import annotations

import logging
import platform
import sys
from pathlib import Path
from typing import Any

import numpy as np

from .hashing import HASH_VERSION

_LOGGER_NAME = "miniseed"


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def configure_logger(log_path: str | Path | None = None) -> logging.Logger:
    """Return the shared logger, attaching a file handler when requested."""
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in logger.handlers
    ):
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(stream)
    if log_path is not None:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(fh)
    return logger


def log_versions(logger: logging.Logger, identity: str) -> None:
    logger.info(
        "identity=%s step=startup verdict=OK python=%s numpy=%s "
        "hash_version=%s platform=%s",
        identity,
        platform.python_version(),
        np.__version__,
        HASH_VERSION,
        platform.platform(),
    )


def log_event(
    logger: logging.Logger,
    *,
    step: str,
    verdict: str,
    identity: str = "-",
    run_id: str = "-",
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Emit one structured provenance line."""
    parts = [
        f"identity={identity}",
        f"run_id={run_id}",
        f"step={step}",
        f"verdict={verdict}",
    ]
    for key, value in fields.items():
        parts.append(f"{key}={_fmt(value)}")
    logger.log(level, " ".join(parts))
