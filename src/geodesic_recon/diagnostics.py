"""Request-scoped diagnostics.

Every log record carries a request id. Image payloads are treated as
sensitive: only aggregate statistics (shape, dtype, min/max/mean) are ever
logged — raw pixel data never leaves the service in a log line.
"""
from __future__ import annotations

import contextvars
import logging
import uuid

import numpy as np

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")
    )
    handler.addFilter(RequestIdFilter())
    root = logging.getLogger("geodesic_recon")
    root.setLevel(level)
    if not root.handlers:
        root.addHandler(handler)
    root.propagate = False


def new_request_id() -> str:
    rid = uuid.uuid4().hex
    _request_id.set(rid)
    return rid


def set_request_id(rid: str) -> None:
    _request_id.set(rid)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"geodesic_recon.{name}")


def image_stats(arr: np.ndarray) -> dict:
    """Aggregate-only, masked view of an image — safe to log."""
    arr = np.asarray(arr)
    return {
        "shape": list(arr.shape),
        "dtype": str(arr.dtype),
        "min": float(arr.min()),
        "max": float(arr.max()),
        "mean": round(float(arr.mean()), 6),
    }
