"""Startup configuration for the graph-cut service.

All values can be overridden through environment variables prefixed with
``GRAPHCUT_`` so the same code runs in tests (tiny limits) and locally
(default limits) without code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class AppConfig:
    """Runtime limits and toggles.

    Attributes:
        max_pixels: hard cap on image pixels per request (resource guard).
        max_edges: hard cap on graph edges (t-links + pairwise) per request.
        chunk_rows: rows per chunk when streaming graph construction.
        data_dir: directory holding local sample fixtures (PNG/JSON).
        seed_weight_headroom: big-M must stay below ``2 ** 52`` minus this
            many ulps of headroom so float64 accumulation cannot overflow
            into loss of integer precision.
        scipy_cross_check: independently re-solve with SciPy's integer
            max-flow on scaled capacities and compare flow values.
        cross_check_max_nodes: skip the SciPy cross-check above this size
            (it duplicates memory); certificate checks still run.
        flow_tolerance: absolute tolerance for flow/cut/energy consistency.
        log_level: Python logging level name for the ``graphcut`` logger.
    """

    max_pixels: int = 65_536          # 256 x 256
    max_edges: int = 2_000_000
    chunk_rows: int = 16
    data_dir: Path = _DEFAULT_DATA_DIR
    seed_weight_headroom: float = 2.0**52
    scipy_cross_check: bool = True
    cross_check_max_nodes: int = 20_000
    flow_tolerance: float = 1e-6
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "AppConfig":
        env = os.environ
        return cls(
            max_pixels=int(env.get("GRAPHCUT_MAX_PIXELS", cls.max_pixels)),
            max_edges=int(env.get("GRAPHCUT_MAX_EDGES", cls.max_edges)),
            chunk_rows=int(env.get("GRAPHCUT_CHUNK_ROWS", cls.chunk_rows)),
            data_dir=Path(env.get("GRAPHCUT_DATA_DIR", str(_DEFAULT_DATA_DIR))),
            scipy_cross_check=env.get("GRAPHCUT_SCIPY_CROSS_CHECK", "1") != "0",
            log_level=env.get("GRAPHCUT_LOG_LEVEL", cls.log_level),
        )
