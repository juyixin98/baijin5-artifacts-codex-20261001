"""DSP package: partitioned convolution engine and IR swap policies."""
from app.convolution.engine import (
    PartitionedConvolver,
    partition_count,
    state_bytes_estimate,
    validate_block_size,
)
from app.convolution.swap import SwappableConvolver, SwapStrategy

__all__ = [
    "PartitionedConvolver",
    "SwappableConvolver",
    "SwapStrategy",
    "partition_count",
    "state_bytes_estimate",
    "validate_block_size",
]
