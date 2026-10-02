"""Request-scoped diagnostic logging.

Every request gets a UUID that appears in the API response, the
Diagnostics payload and the log stream, so a log line can always be
traced back to a request.  Logs carry only desensitized state: shapes,
dtypes, counters and short content-hash prefixes — never pixel data.
"""

from __future__ import annotations

import logging
import uuid

from .schemas import Diagnostics

logger = logging.getLogger("geodesic.service")


def configure_logging(level: int = logging.INFO) -> None:
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s"
            )
        )
        logger.addHandler(handler)
    logger.setLevel(level)


def new_request_id() -> str:
    return uuid.uuid4().hex


def log_diagnostics(diag: Diagnostics) -> None:
    """Emit one structured log line per completed request."""
    logger.info(
        "request_id=%s status=%s category=%s shape=%s dtype=%s "
        "engine=%s connectivity=%s violations=%d iterations=%s "
        "queue_pops=%s changed=%s marker_hash=%s mask_hash=%s reasons=%s",
        diag.request_id,
        diag.status.value,
        diag.failure_category.value if diag.failure_category else "-",
        diag.shape,
        diag.dtype,
        diag.engine,
        diag.connectivity,
        diag.violation_pixels,
        diag.iterations,
        diag.queue_pops,
        diag.changed_pixels,
        diag.marker_sha256_12,
        diag.mask_sha256_12,
        "; ".join(diag.reasons) if diag.reasons else "-",
    )
