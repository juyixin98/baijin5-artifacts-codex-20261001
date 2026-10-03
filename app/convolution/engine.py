"""Uniformly partitioned overlap-add FFT convolution core.

Algorithm
---------
The impulse response ``h`` (length ``L``) is split into
``P = ceil(L / B)`` partitions of ``B`` samples, where ``B`` is the stream
block size.  Every partition is zero-padded to ``N = 2B`` and transformed
once up front into ``ir_spectra[p]``.

Each incoming block of ``B`` samples is zero-padded to ``N``, transformed,
and pushed onto a frequency-domain delay line (FDL).  The output spectrum is
``Y = sum_p FDL[p] * ir_spectra[p]``; the inverse transform yields ``2B``
time-domain samples whose first ``B`` are added to the overlap tail carried
from the previous block (overlap-add).

Because ``N = 2B >= B + B - 1``, the circular convolution of one zero-padded
input block with one zero-padded partition equals their linear convolution,
so the aggregate output is sample-exact linear convolution (up to float64
round-off).  ``flush()`` pushes ``P`` zero blocks so every buffered input
spectrum propagates through every partition, then drains the final overlap
tail — the full ``L - 1`` tail samples beyond the last input sample.

This module is deliberately framework-free and holds no session identity;
per-stream bookkeeping lives in :mod:`app.stream`.
"""
from __future__ import annotations

import numpy as np

from app.errors import InvalidBlockSizeError, InvalidIRError

MIN_BLOCK_SIZE = 8
_COMPLEX128_BYTES = 16
_FLOAT64_BYTES = 8


def validate_block_size(block_size: int) -> None:
    """Block size must be a power of two >= MIN_BLOCK_SIZE (FFT-friendly)."""
    if not isinstance(block_size, int) or block_size < MIN_BLOCK_SIZE or (block_size & (block_size - 1)):
        raise InvalidBlockSizeError(
            f"block_size must be a power of two >= {MIN_BLOCK_SIZE}, got {block_size!r}",
            detail={"block_size": block_size},
        )


def partition_count(ir_length: int, block_size: int) -> int:
    return -(-ir_length // block_size)  # ceil division


def state_bytes_estimate(ir_length: int, block_size: int) -> dict:
    """Projected in-memory state for one convolver, including spectrum history.

    The spectrum history (IR partition spectra + input frequency delay line)
    dominates the budget: two complex128 arrays of shape (P, B + 1).
    """
    partitions = partition_count(ir_length, block_size)
    bins = block_size + 1  # rfft bins of a 2B-point transform
    spectra = partitions * bins * _COMPLEX128_BYTES
    return {
        "ir_spectra": spectra,
        "input_fdl": spectra,
        "overlap_tail": block_size * _FLOAT64_BYTES,
        "total": 2 * spectra + block_size * _FLOAT64_BYTES,
    }


class PartitionedConvolver:
    """One generation of a partitioned convolver (fixed IR, fixed block size)."""

    def __init__(self, ir: np.ndarray, block_size: int):
        validate_block_size(block_size)
        ir = np.asarray(ir, dtype=np.float64)
        if ir.ndim != 1 or ir.size == 0:
            raise InvalidIRError("impulse response must be a non-empty 1-D array")
        if not np.all(np.isfinite(ir)):
            raise InvalidIRError("impulse response contains NaN or infinite values")

        self.block_size = block_size
        self.fft_size = 2 * block_size
        self.ir_length = int(ir.size)
        self.num_partitions = partition_count(self.ir_length, block_size)

        padded = np.zeros(self.num_partitions * block_size, dtype=np.float64)
        padded[: self.ir_length] = ir
        # Spectrum history, part 1: per-partition IR spectra, shape (P, B+1).
        self.ir_spectra = np.fft.rfft(
            padded.reshape(self.num_partitions, block_size), n=self.fft_size, axis=1
        )
        # Spectrum history, part 2: frequency delay line of input block spectra.
        self._fdl = np.zeros_like(self.ir_spectra)
        # Overlap-add tail carried between blocks (second half of the last IFFT).
        self._overlap = np.zeros(block_size, dtype=np.float64)
        self.blocks_processed = 0

    def process_block(self, block: np.ndarray) -> np.ndarray:
        """Consume exactly ``block_size`` samples, emit exactly ``block_size``."""
        block = np.asarray(block, dtype=np.float64)
        if block.size != self.block_size:
            raise ValueError(
                f"process_block expects {self.block_size} samples, got {block.size}"
            )
        spectrum = np.fft.rfft(block, n=self.fft_size)
        # Shift the FDL down by one slot and push the new spectrum in front.
        # NumPy materialises the overlapping RHS before assigning, so this is safe.
        self._fdl[1:] = self._fdl[:-1]
        self._fdl[0] = spectrum

        out_spectrum = np.sum(self._fdl * self.ir_spectra, axis=0)
        block_out = np.fft.irfft(out_spectrum, n=self.fft_size)
        emitted = block_out[: self.block_size] + self._overlap
        self._overlap = block_out[self.block_size :].copy()
        self.blocks_processed += 1
        return emitted

    def flush(self) -> np.ndarray:
        """Drain all remaining state after the last input block.

        Feeds ``num_partitions`` zero blocks so the most recent input spectra
        propagate through every partition, then appends the residual overlap
        tail.  Returns ``(num_partitions + 1) * block_size`` samples; callers
        truncate to the exact owed tail length (``ir_length - 1`` beyond the
        last real input sample).
        """
        parts = [
            self.process_block(np.zeros(self.block_size, dtype=np.float64))
            for _ in range(self.num_partitions)
        ]
        parts.append(self._overlap.copy())
        self._overlap[:] = 0.0
        return np.concatenate(parts)

    def state_bytes(self) -> dict:
        """Actual resident state, including the full spectrum history."""
        return {
            "ir_spectra": int(self.ir_spectra.nbytes),
            "input_fdl": int(self._fdl.nbytes),
            "overlap_tail": int(self._overlap.nbytes),
            "total": int(self.ir_spectra.nbytes + self._fdl.nbytes + self._overlap.nbytes),
        }
