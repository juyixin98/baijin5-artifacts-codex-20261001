"""Sample-block contract validation.

A PCM block is a 2-D array-like of shape (frames, channels) with finite
real values. Validation failures raise ContractError carrying a stable
``category`` string so callers (and the API layer) can report the failure
class instead of collapsing every error into a generic 500/200.
"""

from __future__ import annotations

import numpy as np

MAX_FRAMES_PER_BLOCK = 2_000_000


class ContractError(ValueError):
    """Block-contract violation with a machine-readable category."""

    def __init__(self, category: str, detail: str):
        super().__init__(f"{category}: {detail}")
        self.category = category
        self.detail = detail


def validate_block(pcm, channels: int, *, max_frames: int = MAX_FRAMES_PER_BLOCK) -> np.ndarray:
    """Validate and normalize a PCM block to float64 (frames, channels)."""
    try:
        arr = np.asarray(pcm, dtype=np.float64)
    except (ValueError, TypeError) as exc:
        raise ContractError("bad_dtype", f"pcm is not a numeric 2-D block: {exc}") from exc

    if arr.ndim != 2:
        raise ContractError(
            "bad_shape", f"pcm must be 2-D (frames, channels), got ndim={arr.ndim}"
        )
    if arr.shape[1] != channels:
        raise ContractError(
            "bad_channels",
            f"pcm has {arr.shape[1]} channels, config expects {channels}",
        )
    if arr.shape[0] > max_frames:
        raise ContractError(
            "too_large", f"block has {arr.shape[0]} frames, max is {max_frames}"
        )
    if not np.all(np.isfinite(arr)):
        raise ContractError("non_finite", "pcm contains NaN or infinite samples")
    return np.ascontiguousarray(arr)
