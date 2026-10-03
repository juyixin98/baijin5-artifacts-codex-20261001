"""Per-session streaming state machine.

Wraps :class:`SwappableConvolver` with the boundary semantics of the
service:

* Every block except the final one must contain exactly ``block_size``
  samples.  The final block may be short (non-integral tail) and must be
  marked ``final=True``; it is zero-padded internally.
* After a final block, the only legal next call is :meth:`flush`.
* ``flush()`` emits exactly the owed tail — ``ir_length - 1`` samples beyond
  the last real input sample for the current IR generation — and may be
  called at most once.  Total emitted output then equals
  ``total_input + ir_length - 1`` (per generation; see README for the exact
  accounting under swaps).
* IR swaps reset the per-generation sample accounting; ``restart`` also
  discards the pending tail, ``crossfade`` fades it out.

The "owed tail" invariant: for the current generation, samples owed beyond
what has been emitted equal ``gen_input + ir_length - 1 - gen_output``.
Because every processed block emits ``block_size`` samples and every block
carries at most ``block_size`` real samples, ``gen_input <= gen_output``
always holds, so the owed tail never exceeds ``ir_length - 1`` and always
fits inside the raw flush buffer.
"""
from __future__ import annotations

import numpy as np

from app.convolution import (
    SwappableConvolver,
    SwapStrategy,
    state_bytes_estimate,
    validate_block_size,
)
from app.errors import (
    BlockSizeMismatchError,
    EmptyBlockError,
    InputAfterFinalBlockError,
    InvalidIRError,
    InvalidSamplesError,
    SessionAlreadyFlushedError,
    StateBudgetExceededError,
)


def _validate_ir(ir: np.ndarray) -> np.ndarray:
    ir = np.asarray(ir, dtype=np.float64)
    if ir.ndim != 1 or ir.size == 0:
        raise InvalidIRError("impulse response must be a non-empty 1-D array")
    if not np.all(np.isfinite(ir)):
        raise InvalidIRError("impulse response contains NaN or infinite values")
    return ir


class StreamSession:
    def __init__(
        self,
        session_id: str,
        sample_rate: int,
        block_size: int,
        ir: np.ndarray,
        swap_strategy: SwapStrategy = SwapStrategy.CROSSFADE,
        crossfade_blocks: int = 4,
        max_state_bytes: int | None = None,
    ):
        validate_block_size(block_size)
        ir = _validate_ir(ir)
        self.session_id = session_id
        self.sample_rate = int(sample_rate)
        self.block_size = block_size
        self.max_state_bytes = max_state_bytes

        self.convolver = SwappableConvolver(ir, block_size, swap_strategy, crossfade_blocks)
        self._enforce_budget(self.convolver.state_bytes()["total"])

        # Per-generation accounting (reset on every IR swap).
        self._gen_input = 0
        self._gen_output = 0
        # Whole-session accounting.
        self.total_input = 0
        self.total_output = 0
        self.blocks_processed = 0
        self.final_received = False
        self.flushed = False

    # -- budget --------------------------------------------------------------

    def _enforce_budget(self, projected_bytes: int) -> None:
        if self.max_state_bytes is not None and projected_bytes > self.max_state_bytes:
            raise StateBudgetExceededError(
                f"projected convolver state {projected_bytes} bytes exceeds "
                f"session budget {self.max_state_bytes} bytes",
                detail={
                    "projected_bytes": projected_bytes,
                    "budget_bytes": self.max_state_bytes,
                },
            )

    # -- intake ----------------------------------------------------------------

    def push_block(self, samples: np.ndarray, final: bool = False) -> np.ndarray:
        if self.flushed:
            raise SessionAlreadyFlushedError(
                "session has been flushed; no further input is accepted"
            )
        if self.final_received:
            raise InputAfterFinalBlockError(
                "a final block was already accepted; call flush to drain the tail"
            )
        samples = np.asarray(samples, dtype=np.float64)
        n = samples.size
        if n == 0:
            raise EmptyBlockError("block must contain at least one sample")
        if not np.all(np.isfinite(samples)):
            raise InvalidSamplesError("block contains NaN or infinite values")
        if n > self.block_size or (n != self.block_size and not final):
            raise BlockSizeMismatchError(
                f"expected {self.block_size} samples per block "
                f"(a short block is only legal as the final block), got {n}",
                detail={"expected": self.block_size, "got": n, "final": final},
            )

        block = samples
        if n != self.block_size:
            block = np.zeros(self.block_size, dtype=np.float64)
            block[:n] = samples

        out = self.convolver.process_block(block)
        self._gen_input += n
        self._gen_output += self.block_size
        self.total_input += n
        self.total_output += self.block_size
        self.blocks_processed += 1
        if final:
            self.final_received = True
        return out

    # -- IR swap -----------------------------------------------------------------

    def swap_ir(self, ir: np.ndarray, strategy: SwapStrategy | None = None) -> None:
        if self.flushed:
            raise SessionAlreadyFlushedError(
                "session has been flushed; IR swap is no longer meaningful"
            )
        ir = _validate_ir(ir)
        strategy = SwapStrategy(strategy) if strategy is not None else self.convolver.default_strategy
        # Worst-case projection: during a crossfade both generations coexist.
        new_bytes = state_bytes_estimate(ir.size, self.block_size)["total"]
        projected = (
            self.convolver.state_bytes()["total"] + new_bytes
            if strategy is SwapStrategy.CROSSFADE
            else new_bytes
        )
        self._enforce_budget(projected)
        self.convolver.swap(ir, strategy)
        self._gen_input = 0
        self._gen_output = 0

    # -- drain -------------------------------------------------------------------

    def flush(self) -> np.ndarray:
        if self.flushed:
            raise SessionAlreadyFlushedError("flush may be called at most once")
        raw = self.convolver.flush()
        owed = self._gen_input + self.convolver.current_ir_length - 1 - self._gen_output
        tail = raw[: max(0, owed)]
        self.flushed = True
        self.total_output += tail.size
        return tail

    # -- introspection -------------------------------------------------------------

    def state_report(self) -> dict:
        return {
            "session_id": self.session_id,
            "sample_rate": self.sample_rate,
            "block_size": self.block_size,
            "ir_length": self.convolver.current_ir_length,
            "num_partitions": self.convolver.num_partitions,
            "blocks_processed": self.blocks_processed,
            "total_input_samples": self.total_input,
            "total_output_samples": self.total_output,
            "final_received": self.final_received,
            "flushed": self.flushed,
            "fade_remaining_blocks": self.convolver.fade_remaining_blocks,
            "state_bytes": self.convolver.state_bytes(),
            "budget_bytes": self.max_state_bytes,
        }
