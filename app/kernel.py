"""Thinning numeric kernel: fully-parallel Zhang-Suen sub-iterations.

Contract implemented here:
- Compatible adjacency: foreground is 8-connected, background is 4-connected.
  The deletion conditions (A==1, 2<=B<=6) only remove *simple* points, so
  foreground components never split and background holes never open.
- Synchronous deletion: every sub-iteration computes its deletion mask from
  ONE pre-state and applies it afterwards. No pixel decision in a
  sub-iteration can observe another deletion from the same sub-iteration.
- Endpoints (B==1) are never deleted; holes are preserved because removing a
  simple point cannot merge a background component with another one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

ALGORITHM_ID = "zhang-suen-parallel"


def _neighbors(img: np.ndarray) -> tuple[np.ndarray, ...]:
    """Return P2..P9 (clockwise from north) for every pixel, zero-padded."""
    p = np.pad(img, 1, mode="constant")
    p2 = p[:-2, 1:-1]
    p3 = p[:-2, 2:]
    p4 = p[1:-1, 2:]
    p5 = p[2:, 2:]
    p6 = p[2:, 1:-1]
    p7 = p[2:, :-2]
    p8 = p[1:-1, :-2]
    p9 = p[:-2, :-2]
    return p2, p3, p4, p5, p6, p7, p8, p9


def deletion_mask(img: np.ndarray, subiteration: int) -> np.ndarray:
    """Boolean mask of foreground pixels deletable in this sub-iteration.

    The mask depends only on `img` as passed in (the pre-state); callers must
    apply it after the full mask is computed to keep deletion synchronous.
    """
    if subiteration not in (1, 2):
        raise ValueError(f"subiteration must be 1 or 2, got {subiteration}")
    p2, p3, p4, p5, p6, p7, p8, p9 = _neighbors(img)
    b = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9  # foreground neighbor count
    seq = (p2, p3, p4, p5, p6, p7, p8, p9, p2)
    a = np.zeros(img.shape, dtype=np.uint8)
    for i in range(8):  # 0->1 transitions around the ring = connectivity number
        a += (seq[i] == 0) & (seq[i + 1] == 1)
    if subiteration == 1:
        g1 = (p2 * p4 * p6) == 0
        g2 = (p4 * p6 * p8) == 0
    else:
        g1 = (p2 * p4 * p8) == 0
        g2 = (p2 * p6 * p8) == 0
    return (img == 1) & (b >= 2) & (b <= 6) & (a == 1) & g1 & g2


@dataclass
class RoundRecord:
    """One full round = sub-iteration 1 + sub-iteration 2."""

    round_index: int
    sub1_deleted: int
    sub2_deleted: int

    @property
    def deleted(self) -> int:
        return self.sub1_deleted + self.sub2_deleted


@dataclass
class ThinningResult:
    skeleton: np.ndarray
    rounds: list[RoundRecord] = field(default_factory=list)
    total_deleted: int = 0


def apply_rounds(work: np.ndarray, max_rounds: int, mask_fn) -> tuple[np.ndarray, list[RoundRecord]]:
    """Shared round loop. `mask_fn(state, subiteration)` must return the
    deletion mask for the whole image, computed from `state` alone."""
    records: list[RoundRecord] = []
    for round_index in range(max_rounds):
        m1 = mask_fn(work, 1)
        n1 = int(m1.sum())
        work = work.copy()
        work[m1] = 0
        m2 = mask_fn(work, 2)
        n2 = int(m2.sum())
        work = work.copy()
        work[m2] = 0
        records.append(RoundRecord(round_index, n1, n2))
        if n1 == 0 and n2 == 0:
            break
    return work, records


def thin(image: np.ndarray, max_rounds: int = 10_000) -> ThinningResult:
    """Full-image thinning (reference path). Input must satisfy the contract."""
    img = (image != 0).astype(np.uint8)
    before = int(img.sum())
    skeleton, records = apply_rounds(img, max_rounds, deletion_mask)
    return ThinningResult(
        skeleton=skeleton,
        rounds=records,
        total_deleted=before - int(skeleton.sum()),
    )
