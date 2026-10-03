"""Shared pytest fixtures: deterministic synthetic signals and a helper that
runs a full stream (blocks + flush) through a StreamSession."""
from __future__ import annotations

import numpy as np
import pytest

from app.convolution import SwapStrategy
from app.stream import StreamSession


def run_stream(
    x: np.ndarray,
    ir: np.ndarray,
    block_size: int,
    swap_strategy: SwapStrategy = SwapStrategy.CROSSFADE,
    crossfade_blocks: int = 4,
) -> np.ndarray:
    """Feed ``x`` through a session in block_size chunks and return the full
    output including the flushed tail."""
    session = StreamSession(
        session_id="test",
        sample_rate=48_000,
        block_size=block_size,
        ir=ir,
        swap_strategy=swap_strategy,
        crossfade_blocks=crossfade_blocks,
    )
    outs = []
    n = len(x)
    for start in range(0, n, block_size):
        chunk = x[start : start + block_size]
        outs.append(session.push_block(chunk, final=start + block_size >= n))
    outs.append(session.flush())
    return np.concatenate(outs)


@pytest.fixture()
def rng() -> np.random.Generator:
    return np.random.Generator(np.random.PCG64(20261003))
