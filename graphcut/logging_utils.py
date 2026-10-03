"""Structured run logging.

Every pipeline stage logs one line shaped like::

    run_id=<uuid> event=<stage> key=value ...

so a failing run can be replayed from its ``run_id``: the job record and the
log stream share the same identifier, and each event records the key
intermediate state (graph sizes, flow value, energy decomposition) plus the
judgment reason for rejections.
"""

from __future__ import annotations

import logging
from typing import Any

LOGGER_NAME = "graphcut"


def _render(value: Any) -> str:
    """Render a field value; numpy scalars become plain Python scalars."""
    item = getattr(value, "item", None)
    if callable(item) and not isinstance(value, (str, bytes, dict, list)):
        try:
            value = item()
        except (ValueError, TypeError):
            pass
    return repr(value)


def get_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(handler)
        logger.propagate = True  # let pytest caplog see records
    return logger


def configure_logging(level: str) -> None:
    get_logger().setLevel(level.upper())


def log_event(
    logger: logging.Logger,
    level: int,
    run_id: str,
    event: str,
    **fields: object,
) -> None:
    """Emit one structured log line for a pipeline stage."""
    kv = " ".join(f"{key}={_render(value)}" for key, value in sorted(fields.items()))
    message = f"run_id={run_id} event={event}"
    if kv:
        message = f"{message} {kv}"
    logger.log(level, message)
