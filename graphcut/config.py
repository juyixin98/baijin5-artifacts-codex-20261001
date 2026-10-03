"""Runtime configuration for the graph-cut service.

Values are read from environment variables with the ``GRAPHCUT_`` prefix so
the service can be configured without code changes, and tests can inject a
custom :class:`Settings` instance instead of touching the process env.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw is None else int(raw)


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return default if raw is None else float(raw)


@dataclass(frozen=True)
class Settings:
    """Capacity and numerical-tolerance knobs.

    max_pixels:        hard limit on H*W; larger images are rejected with
                       RESOURCE_EXHAUSTED / IMAGE_TOO_LARGE.
    max_jobs:          maximum number of jobs tracked at once (pending+running
                       + retained results); overflow -> JOB_QUEUE_FULL.
    chunk_rows:        rows of the image processed per cooperative chunk when
                       building the s-t graph inside a job (progress/cancel
                       granularity).
    energy_tolerance:  absolute+relative tolerance used when cross-checking
                       flow value, cut capacity and independently evaluated
                       energy in the cut certificate.
    big_m_headroom:    hard-seed capacity M must satisfy M <= 2**big_m_headroom
                       so that float64 arithmetic on capacities stays exact
                       enough for the certificate (default 2**52).
    """

    max_pixels: int = 1_000_000
    max_jobs: int = 64
    chunk_rows: int = 64
    energy_tolerance: float = 1e-6
    big_m_headroom: int = 52

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            max_pixels=_get_int("GRAPHCUT_MAX_PIXELS", cls.max_pixels),
            max_jobs=_get_int("GRAPHCUT_MAX_JOBS", cls.max_jobs),
            chunk_rows=_get_int("GRAPHCUT_CHUNK_ROWS", cls.chunk_rows),
            energy_tolerance=_get_float("GRAPHCUT_ENERGY_TOLERANCE", cls.energy_tolerance),
            big_m_headroom=_get_int("GRAPHCUT_BIG_M_HEADROOM", cls.big_m_headroom),
        )
