"""Streaming lookahead limiter for synthetic PCM.

Public API:
    LimiterConfig   -- fixed algorithm parameters and validation
    LimiterStream   -- block-wise streaming processor with lookahead latency
    limit_offline   -- whole-signal convenience wrapper (delay-compensated)
    RunLog          -- structured per-run logging (run id, versions, hashes)
"""

from .config import LimiterConfig
from .stream import LimiterStream
from .offline import OfflineResult, limit_offline
from .runlog import RunLog, library_versions

__all__ = [
    "LimiterConfig",
    "LimiterStream",
    "OfflineResult",
    "limit_offline",
    "RunLog",
    "library_versions",
]
