"""Streaming lookahead limiter for synthetic PCM.

Layers:
- config:    fixed algorithm contract (detection range, attack/release, linking)
- contract:  sample-block contract validation
- envelope:  pure gain-math primitives
- stream:    streaming state (lookahead ring buffer, latency, flush)
- fixtures:  deterministic synthetic PCM fixtures
- api:       FastAPI service layer
"""

from .config import ALGORITHM_CONTRACT_VERSION, LimiterConfig
from .contract import ContractError, validate_block
from .stream import BlockResult, OfflineResult, StreamingLimiter, process_offline

__all__ = [
    "ALGORITHM_CONTRACT_VERSION",
    "LimiterConfig",
    "ContractError",
    "validate_block",
    "BlockResult",
    "OfflineResult",
    "StreamingLimiter",
    "process_offline",
]
