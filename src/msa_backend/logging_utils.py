"""Structured-ish logging helpers.

Every pipeline step logs with the run id attached so test and server logs
can be correlated back to a specific input. Tests additionally use
``log_judgement`` to record the *basis* of an assertion next to it.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger("msa_backend")
    root.addHandler(handler)
    root.setLevel(level.upper())
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"msa_backend.{name}")


def log_judgement(logger: logging.Logger, run_identity: str, step: str, basis: str) -> None:
    """Record a verifiable judgement line: who, which step, on what basis."""
    logger.info("judgement run=%s step=%s basis=%s", run_identity, step, basis)
