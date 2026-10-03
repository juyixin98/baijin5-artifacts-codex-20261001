"""Whole-signal convenience wrapper with delay compensation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import LimiterConfig
from .runlog import RunLog
from .stream import LimiterStream


@dataclass
class OfflineResult:
    output: np.ndarray  # delay-compensated, same length as input
    gain: np.ndarray  # per-sample gain aligned with input
    latency_samples: int
    stats: dict


def limit_offline(
    config: LimiterConfig,
    pcm: np.ndarray,
    block_size: int | None = None,
    run_log: RunLog | None = None,
) -> OfflineResult:
    """Process a whole signal and re-align output with input.

    The raw stream output is delayed by ``latency_samples``; compensation
    drops those leading samples so ``output[i]`` corresponds to ``pcm[i]``.
    """
    pcm = np.asarray(pcm, dtype=np.float64)
    if pcm.ndim == 1:
        pcm = pcm[:, None]
    stream = LimiterStream(config, num_channels=pcm.shape[1], run_log=run_log)
    block_size = block_size or max(pcm.shape[0], 1)
    outs, gains = [], []
    for start in range(0, pcm.shape[0], block_size):
        o, g = stream.process(pcm[start : start + block_size])
        outs.append(o)
        gains.append(g)
    o, g = stream.flush()
    outs.append(o)
    gains.append(g)
    out = np.vstack(outs)
    gain = np.concatenate(gains)
    latency = stream.latency_samples
    return OfflineResult(
        output=out[latency:],
        gain=gain[latency:],
        latency_samples=latency,
        stats=stream.stats,
    )
