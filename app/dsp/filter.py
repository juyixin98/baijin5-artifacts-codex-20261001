"""SOS cascade filter engine with explicit per-channel, per-section state.

Numerical structure
-------------------
Each second-order section is implemented in transposed direct form II (DF2T),
matching ``scipy.signal.lfilter``'s ``zi`` semantics:

    y[n] = b0 * x[n] + z0
    z0   = b1 * x[n] - a1 * y[n] + z1
    z1   = b2 * x[n] - a2 * y[n]

State layout is ``(n_sections, n_channels, 2)``: every channel of every
section owns an isolated delay pair, so channels never interact and chunk
boundaries are invisible — processing ``x`` as one block or as any partition
of contiguous chunks yields bit-identical output, because the per-sample
operation order does not depend on chunking.

Failure contract
----------------
- Input samples must be finite (``SampleValidationError`` otherwise).
- After every chunk, both the output block and the carried state are checked
  for finiteness. Overflow or non-finite values raise ``ComputationError``
  with the first offending location; nothing is silently clamped or zeroed.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

from app.config import MAX_CHANNELS, MAX_CHUNK_SAMPLES
from app.errors import (
    ComputationError,
    ResourceLimitError,
    SampleValidationError,
)
from app.dsp.coefficients import NormalizedSOS


class SOSCascadeFilter:
    """Stateful multi-channel SOS cascade.

    A fresh instance starts with zero initial conditions (zero-state
    response). State persists across ``process`` calls until ``reset``.
    """

    def __init__(self, sos: NormalizedSOS, n_channels: int) -> None:
        if not 1 <= n_channels <= MAX_CHANNELS:
            raise ResourceLimitError(
                f"n_channels must be in [1, {MAX_CHANNELS}], got {n_channels}",
                detail={"n_channels": n_channels, "limit": MAX_CHANNELS},
            )
        self._sos = sos
        self._n_channels = n_channels
        self._state = np.zeros((sos.n_sections, n_channels, 2), dtype=np.float64)

    @property
    def n_channels(self) -> int:
        return self._n_channels

    @property
    def n_sections(self) -> int:
        return self._sos.n_sections

    @property
    def state(self) -> np.ndarray:
        """Read-only view of the DF2T delay states, shape (sections, channels, 2)."""
        view = self._state.view()
        view.flags.writeable = False
        return view

    def reset(self) -> None:
        """Zero all delay states (clean-restart transient policy)."""
        self._state[...] = 0.0

    def replace_coefficients(self, sos: NormalizedSOS, *, keep_state: bool) -> None:
        """Swap coefficients, applying the stream's transient policy.

        ``keep_state=True``  ("carry"): delay states are preserved if the new
            cascade has the same section count; otherwise states are zeroed
            (state layout is per-section, so a different count has no
            meaningful carry-over).
        ``keep_state=False`` ("reset"): all delay states are zeroed.
        """
        if keep_state and sos.n_sections == self._sos.n_sections:
            self._sos = sos
            return
        self._sos = sos
        self._state = np.zeros(
            (sos.n_sections, self._n_channels, 2), dtype=np.float64
        )

    def process(self, samples: np.ndarray) -> np.ndarray:
        """Filter one chunk of shape ``(n_channels, n_samples)``.

        Returns a new array; the input is never mutated.
        """
        x = self._validate_input(samples)
        y = x.copy()
        if y.shape[1] == 0:
            return y  # empty chunk: no-op, state untouched
        for s in range(self._sos.n_sections):
            b = self._sos.sections[s, :3]
            a = self._sos.sections[s, 3:]
            y, zf = lfilter(b, a, y, axis=-1, zi=self._state[s])
            self._state[s] = zf
        self._check_finite(y)
        return y

    def _validate_input(self, samples: np.ndarray) -> np.ndarray:
        x = np.asarray(samples, dtype=np.float64)
        if x.ndim != 2 or x.shape[0] != self._n_channels:
            raise SampleValidationError(
                f"samples must have shape ({self._n_channels}, n_samples), "
                f"got {list(x.shape)}",
                detail={"expected_channels": self._n_channels,
                        "received_shape": list(x.shape)},
            )
        if x.shape[1] > MAX_CHUNK_SAMPLES:
            raise ResourceLimitError(
                f"chunk too long: {x.shape[1]} > {MAX_CHUNK_SAMPLES} samples",
                detail={"n_samples": int(x.shape[1]), "limit": MAX_CHUNK_SAMPLES},
            )
        if not np.isfinite(x).all():
            bad = np.argwhere(~np.isfinite(x))
            raise SampleValidationError(
                "samples contain non-finite values",
                detail={"first_bad_index": [int(bad[0][0]), int(bad[0][1])]},
            )
        return x

    def _check_finite(self, y: np.ndarray) -> None:
        if np.isfinite(y).all() and np.isfinite(self._state).all():
            return
        if not np.isfinite(y).all():
            bad = np.argwhere(~np.isfinite(y))[0]
            where: dict[str, int | str] = {
                "channel": int(bad[0]),
                "sample": int(bad[1]),
            }
        else:
            bad = np.argwhere(~np.isfinite(self._state))[0]
            where = {
                "section": int(bad[0]),
                "channel": int(bad[1]),
                "delay": int(bad[2]),
            }
        raise ComputationError(
            "filter produced non-finite values (numerical overflow); "
            "output discarded, not zeroed",
            detail={"first_non_finite": where},
        )
