"""Run identity and structured logging.

Every analysis gets a ``run_id`` (UUID4). All log records emitted while
processing that run carry the run id, the caller-supplied ``input_label``
(so logs can be tied back to a specific fixture / data file), library
versions and progress step. Failures are logged and re-raised as explicit
error categories -- nothing is collapsed into a generic success.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.config import Settings

_current: ContextVar["RunContext | None"] = ContextVar("run_context", default=None)
_configure_lock = threading.Lock()
_configured = False


@dataclass(frozen=True)
class RunContext:
    run_id: str
    input_label: str
    versions: dict[str, str]
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


def new_run_id() -> str:
    return str(uuid.uuid4())


def configure_logging(level: str = "INFO") -> None:
    global _configured
    with _configure_lock:
        if _configured:
            return
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        root = logging.getLogger("rd")
        root.handlers[:] = [handler]
        root.setLevel(level)
        root.propagate = False
        _configured = True


def bind_context(run_id: str, input_label: str, settings: Settings) -> RunContext:
    ctx = RunContext(
        run_id=run_id, input_label=input_label, versions=settings.versions()
    )
    _current.set(ctx)
    return ctx


def current_context() -> RunContext | None:
    return _current.get()


def _emit(level: int, event: str, step: str, **fields: Any) -> None:
    logger = logging.getLogger("rd")
    ctx = _current.get()
    payload: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": logging.getLevelName(level),
        "event": event,
        "step": step,
    }
    if ctx is not None:
        payload["run_id"] = ctx.run_id
        payload["input_label"] = ctx.input_label
        payload["versions"] = ctx.versions
    payload.update(fields)
    logger.log(level, json.dumps(payload, ensure_ascii=False, default=str))


def run_start(input_label: str, n: int, cutoff: float, method: str) -> None:
    _emit(
        logging.INFO,
        "run_start",
        "init",
        input_label=input_label,
        n=n,
        cutoff=cutoff,
        method=method,
    )


def step(step_name: str, message: str, **fields: Any) -> None:
    _emit(logging.INFO, "step", step_name, message=message, **fields)


def warn(event: str, step_name: str, message: str, **fields: Any) -> None:
    _emit(logging.WARNING, event, step_name, message=message, **fields)


def failure(event: str, step_name: str, message: str, **fields: Any) -> None:
    _emit(logging.ERROR, event, step_name, message=message, **fields)
