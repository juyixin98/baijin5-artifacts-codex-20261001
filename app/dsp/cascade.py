"""Cascaded second-order-section IIR filter core.

Structure: transposed Direct-Form II per section, state held per
(section, channel) so channels never share delay elements:

    y[n]  = b0 * x[n] + z1
    z1'   = b1 * x[n] - a1 * y[n] + z2
    z2'   = b2 * x[n] - a2 * y[n]

Block semantics are atomic: the per-section state is only committed after
the whole block has been computed and verified finite. A block that
overflows (non-finite output) raises NonFiniteOutputError and leaves the
stream state untouched -- overflow is never silently zeroed.
"""

from __future__ import annotations

import enum

import numpy as np

from app.errors import NonFiniteOutputError, SampleBlockError


class InitialCondition(str, enum.Enum):
    """Policy for the filter state when a stream is created."""

    ZERO = "zero"  # z1 = z2 = 0: filter starts at rest
    DC_STEADY = "dc_steady"  # state pre-charged to the unit-step steady state


class TransientStrategy(str, enum.Enum):
    """Policy for the state when coefficients are switched mid-stream."""

    PRESERVE_STATE = "preserve_state"  # keep z (documented switching transient)
    RESET_STATE = "reset_state"  # zero z: clean restart, discontinuous output


def dc_steady_state(sos: np.ndarray, num_channels: int) -> np.ndarray:
    """Transposed-DF-II state corresponding to a settled unit-step input.

    For constant input u, steady state satisfies
        z1 = y_ss - b0 u,  z2 = b2 u - a1 * y_ss
    with y_ss = u * sum(b) / sum(a). Sections are chained, so each
    section's steady input is the previous section's steady output.
    """
    zi = np.zeros((sos.shape[0], num_channels, 2), dtype=np.float64)
    u = 1.0
    for s in range(sos.shape[0]):
        b0, b1, b2, _, a1, a2 = sos[s]
        denom = 1.0 + a1 + a2
        # denom == 0 implies a pole at z = -1 (marginally stable); such
        # filters are rejected at validation, so this is a safety net.
        if denom == 0.0:
            raise SampleBlockError(
                f"section {s} has no DC steady state (pole at z=-1)"
            )
        y_ss = u * (b0 + b1 + b2) / denom
        zi[s, :, 0] = y_ss - b0 * u
        zi[s, :, 1] = b2 * u - a2 * y_ss
        u = y_ss
    return zi


class SosCascadeFilter:
    """Stateful multi-channel SOS cascade.

    Args:
        sos: normalized (n_sections, 6) coefficients (a0 == 1 per row).
        num_channels: number of independent audio channels.
        initial_condition: how to initialize the per-section state.
    """

    def __init__(
        self,
        sos: np.ndarray,
        num_channels: int,
        initial_condition: InitialCondition = InitialCondition.ZERO,
    ):
        if num_channels < 1:
            raise SampleBlockError("num_channels must be >= 1")
        self._sos = np.asarray(sos, dtype=np.float64)
        self.num_channels = num_channels
        if initial_condition is InitialCondition.DC_STEADY:
            self._zi = dc_steady_state(self._sos, num_channels)
        else:
            self._zi = np.zeros((self._sos.shape[0], num_channels, 2))

    @property
    def n_sections(self) -> int:
        return int(self._sos.shape[0])

    @property
    def coefficients(self) -> np.ndarray:
        return self._sos.copy()

    def state_snapshot(self) -> np.ndarray:
        """Copy of the internal state, for diagnostics and test logging."""
        return self._zi.copy()

    def reset_state(self) -> None:
        self._zi[...] = 0.0

    def replace_coefficients(
        self, sos: np.ndarray, strategy: TransientStrategy
    ) -> None:
        """Switch coefficients mid-stream per the transient strategy."""
        self._sos = np.asarray(sos, dtype=np.float64)
        if strategy is TransientStrategy.RESET_STATE or self._zi.shape[0] != self._sos.shape[0]:
            # A changed section count makes old state meaningless: reset.
            self._zi = np.zeros((self._sos.shape[0], self.num_channels, 2))

    def process_block(self, block: np.ndarray) -> np.ndarray:
        """Filter one block of shape (n_samples, num_channels).

        Returns a new (n_samples, num_channels) array. State commits only
        if every output sample is finite.
        """
        x = np.asarray(block, dtype=np.float64)
        if x.ndim != 2 or x.shape[1] != self.num_channels:
            raise SampleBlockError(
                f"block must have shape (n_samples, {self.num_channels}), "
                f"got {list(x.shape)}",
                context={"shape": list(x.shape)},
            )
        if not np.all(np.isfinite(x)):
            raise SampleBlockError("block contains NaN or Inf samples")

        out = np.empty_like(x)
        # Local state copies: committed to self._zi only on success.
        zi_next = self._zi.copy()
        for s in range(self._sos.shape[0]):
            b0, b1, b2, _, a1, a2 = self._sos[s]
            z1 = zi_next[s, :, 0]
            z2 = zi_next[s, :, 1]
            src = x if s == 0 else out
            dst = out
            for n in range(x.shape[0]):
                xn = src[n]
                yn = b0 * xn + z1
                z1 = b1 * xn - a1 * yn + z2
                z2 = b2 * xn - a2 * yn
                dst[n] = yn
            zi_next[s, :, 0] = z1
            zi_next[s, :, 1] = z2

        if not np.all(np.isfinite(out)):
            bad = int(np.argmin(np.isfinite(out).all(axis=1)))
            raise NonFiniteOutputError(
                "filter output went non-finite (overflow/NaN); block rejected, "
                "stream state left unchanged",
                context={"first_bad_sample": bad},
            )
        self._zi = zi_next
        return out
