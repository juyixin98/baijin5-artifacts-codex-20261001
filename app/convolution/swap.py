"""Impulse-response swap policies on top of the partitioned convolver.

Two explicit strategies are supported:

* ``restart``   — hard cut.  The old convolver (including its spectrum
  history and pending tail) is discarded and a cold convolver starts on the
  new IR.  Sample-exact, but the old IR's tail is truncated immediately.
* ``crossfade`` — the old and new convolvers run in parallel for
  ``crossfade_blocks`` blocks while a linear ramp fades the old output out
  and the new output in.  This masks the transition click, at the cost of a
  transition region that is *not* sample-exact linear convolution: the new
  convolver starts cold, so input history older than the swap point is
  missing from its output until its FDL has refilled, and the old IR's tail
  beyond the fade window is dropped.  For sample-exact transparency the
  input must be silent for at least ``ir_length`` samples around the swap
  and the fade must span at least ``num_partitions`` blocks.

Both strategies preserve the block-in/block-out contract: every processed
block emits exactly ``block_size`` samples regardless of swaps.
"""
from __future__ import annotations

import enum

import numpy as np

from app.convolution.engine import PartitionedConvolver


class SwapStrategy(str, enum.Enum):
    RESTART = "restart"
    CROSSFADE = "crossfade"


class SwappableConvolver:
    """Block convolver whose impulse response can be swapped mid-stream."""

    def __init__(
        self,
        ir: np.ndarray,
        block_size: int,
        strategy: SwapStrategy = SwapStrategy.CROSSFADE,
        crossfade_blocks: int = 4,
    ):
        if crossfade_blocks < 1:
            raise ValueError("crossfade_blocks must be >= 1")
        self.block_size = block_size
        self.default_strategy = SwapStrategy(strategy)
        self.crossfade_blocks = int(crossfade_blocks)
        self._current = PartitionedConvolver(ir, block_size)
        self._old: PartitionedConvolver | None = None
        self._fade_total = 0
        self._fade_remaining = 0

    # -- introspection -----------------------------------------------------

    @property
    def current_ir_length(self) -> int:
        return self._current.ir_length

    @property
    def num_partitions(self) -> int:
        return self._current.num_partitions

    @property
    def fade_remaining_blocks(self) -> int:
        return self._fade_remaining

    @property
    def blocks_processed(self) -> int:
        return self._current.blocks_processed

    def state_bytes(self) -> dict:
        """Resident state; during a crossfade both generations are counted."""
        current = self._current.state_bytes()
        old = self._old.state_bytes() if self._old is not None else None
        return {
            "ir_spectra": current["ir_spectra"],
            "input_fdl": current["input_fdl"],
            "overlap_tail": current["overlap_tail"],
            "crossfade_old": old["total"] if old else 0,
            "total": current["total"] + (old["total"] if old else 0),
        }

    # -- mutation ------------------------------------------------------------

    def swap(self, ir: np.ndarray, strategy: SwapStrategy | None = None) -> None:
        strategy = SwapStrategy(strategy) if strategy is not None else self.default_strategy
        if strategy is SwapStrategy.RESTART:
            self._current = PartitionedConvolver(ir, self.block_size)
            self._old = None
            self._fade_total = 0
            self._fade_remaining = 0
            return
        # crossfade: retire the current convolver (any previous fade is cut short)
        self._old = self._current
        self._current = PartitionedConvolver(ir, self.block_size)
        self._fade_total = self.crossfade_blocks
        self._fade_remaining = self.crossfade_blocks

    # -- processing ----------------------------------------------------------

    def process_block(self, block: np.ndarray) -> np.ndarray:
        if self._old is None:
            return self._current.process_block(block)

        new_out = self._current.process_block(block)
        old_out = self._old.process_block(block)
        done_blocks = self._fade_total - self._fade_remaining
        fade_len = self._fade_total * self.block_size
        # Per-sample linear ramp over the whole fade window, ending exactly at 1.
        ramp = (done_blocks * self.block_size + np.arange(1, self.block_size + 1)) / fade_len
        ramp = np.clip(ramp, 0.0, 1.0)
        out = (1.0 - ramp) * old_out + ramp * new_out
        self._fade_remaining -= 1
        if self._fade_remaining == 0:
            self._old = None
        return out

    def flush(self) -> np.ndarray:
        """Complete any in-flight fade on zero input, then drain the current IR.

        The old convolver's tail beyond the fade window is truncated by
        design (documented crossfade boundary semantic).
        """
        parts = []
        while self._old is not None:
            parts.append(self.process_block(np.zeros(self.block_size, dtype=np.float64)))
        parts.append(self._current.flush())
        return np.concatenate(parts)
