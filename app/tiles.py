"""Tiled thinning jobs with halo exchange and synchronized convergence.

Tiles never thin independently. Each sub-iteration:
  1. the shared pre-state is padded once (halo materialization),
  2. every tile computes deletion candidates for its interior from a read-only
     window that includes a 1-pixel halo of its neighbors' CURRENT pixels,
  3. all tiles' candidates are applied to the shared state at once
     (synchronous commit),
  4. convergence is decided globally: only when every tile reports zero
     deletions in a full round does the schedule stop.

Because Zhang-Suen decisions depend only on the 3x3 pre-state neighborhood,
this schedule is exactly equivalent to the full-image kernel; the tests
assert that equivalence on strokes crossing tile boundaries.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

from .kernel import RoundRecord, ThinningResult, deletion_mask

HALO = 1  # Zhang-Suen needs exactly one ring of neighbor context


@dataclass(frozen=True)
class TileJob:
    """A rectangular interior region; halo is read from the shared state."""

    tile_id: str
    row0: int
    col0: int
    row1: int  # exclusive
    col1: int  # exclusive

    def window(self, padded_state: np.ndarray) -> np.ndarray:
        """Interior plus 1-px halo, read from the padded shared pre-state."""
        return padded_state[
            self.row0 : self.row1 + 2 * HALO,
            self.col0 : self.col1 + 2 * HALO,
        ]

    def candidates(self, padded_state: np.ndarray, subiteration: int) -> np.ndarray:
        """Deletion mask for this tile's interior, from the shared pre-state."""
        mask_with_halo = deletion_mask(self.window(padded_state), subiteration)
        return mask_with_halo[HALO:-HALO, HALO:-HALO]


def plan_tiles(height: int, width: int, tile_size: int) -> list[TileJob]:
    if tile_size < 1:
        raise ValueError(f"tile_size must be >= 1, got {tile_size}")
    jobs: list[TileJob] = []
    for r0 in range(0, height, tile_size):
        for c0 in range(0, width, tile_size):
            jobs.append(
                TileJob(
                    tile_id=f"tile_r{r0}_c{c0}",
                    row0=r0,
                    col0=c0,
                    row1=min(r0 + tile_size, height),
                    col1=min(c0 + tile_size, width),
                )
            )
    return jobs


@dataclass
class TiledThinningResult(ThinningResult):
    tile_size: int = 0
    tile_count: int = 0
    halo_exchanges: int = 0  # one per sub-iteration: halo materialized + read


class TiledThinner:
    def __init__(self, tile_size: int = 64, max_rounds: int = 10_000, workers: int = 1):
        self.tile_size = tile_size
        self.max_rounds = max_rounds
        self.workers = max(1, workers)

    def thin(self, image: np.ndarray) -> TiledThinningResult:
        img = (image != 0).astype(np.uint8)
        before = int(img.sum())
        jobs = plan_tiles(img.shape[0], img.shape[1], self.tile_size)
        work = img.copy()
        records: list[RoundRecord] = []
        halo_exchanges = 0

        for round_index in range(self.max_rounds):
            deleted_per_sub: list[int] = []
            for sub in (1, 2):
                # Halo exchange: materialize neighbor context from the shared
                # pre-state; tiles only read, never write, during this phase.
                padded = np.pad(work, HALO, mode="constant")
                halo_exchanges += 1
                if self.workers > 1 and len(jobs) > 1:
                    with ThreadPoolExecutor(max_workers=self.workers) as pool:
                        masks = list(pool.map(lambda j: j.candidates(padded, sub), jobs))
                else:
                    masks = [j.candidates(padded, sub) for j in jobs]
                # Synchronous commit: apply only after every tile decided.
                deleted = 0
                for job, mask in zip(jobs, masks):
                    region = work[job.row0 : job.row1, job.col0 : job.col1]
                    deleted += int(mask.sum())
                    region[mask] = 0
                deleted_per_sub.append(deleted)
            records.append(RoundRecord(round_index, deleted_per_sub[0], deleted_per_sub[1]))
            # Global convergence: no tile deleted anything in the full round.
            if deleted_per_sub[0] == 0 and deleted_per_sub[1] == 0:
                break

        return TiledThinningResult(
            skeleton=work,
            rounds=records,
            total_deleted=before - int(work.sum()),
            tile_size=self.tile_size,
            tile_count=len(jobs),
            halo_exchanges=halo_exchanges,
        )
