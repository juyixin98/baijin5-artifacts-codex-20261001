"""Seam removal and original-coordinate mapping.

``CarveState`` tracks the current image, the current protection mask and
``col_map`` -- for every row, the original column index of each current
pixel. Every removal produces a NEW state (immutable update); energy is
recomputed from the new image on the next iteration, never reused from
before the removal (acceptance rule 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from .config import SeamConfig
from .contracts import CarveReport, SeamPath
from .errors import ContractViolationError
from .kernel import SeamResult, find_seam


@dataclass(frozen=True)
class CarveState:
    image: np.ndarray
    protect_mask: np.ndarray
    col_map: np.ndarray  # H x W_current int64: original column per current pixel

    @classmethod
    def initial(cls, image: np.ndarray, protect_mask: np.ndarray) -> "CarveState":
        height, width = image.shape[:2]
        if protect_mask.shape != (height, width):
            raise ContractViolationError(
                f"mask shape {protect_mask.shape} != image shape {(height, width)}"
            )
        col_map = np.broadcast_to(np.arange(width, dtype=np.int64), (height, width)).copy()
        return cls(image=image, protect_mask=protect_mask, col_map=col_map)

    @property
    def width(self) -> int:
        return int(self.image.shape[1])


def remove_seam(state: CarveState, columns: tuple[int, ...]) -> CarveState:
    """Remove one pixel per row; return a NEW state (inputs are not mutated)."""
    height, width = state.image.shape[:2]
    if len(columns) != height:
        raise ContractViolationError(
            f"seam has {len(columns)} columns, image has {height} rows"
        )
    if width < 2:
        raise ContractViolationError("cannot remove a seam from a width-1 image")
    cols = np.asarray(columns, dtype=np.int64)
    if (cols < 0).any() or (cols >= width).any():
        raise ContractViolationError(f"seam columns out of range for width {width}")

    keep = np.ones((height, width), dtype=bool)
    keep[np.arange(height), cols] = False

    if state.image.ndim == 2:
        new_image = state.image[keep].reshape(height, width - 1)
    else:
        new_image = state.image[keep].reshape(height, width - 1, state.image.shape[2])
    new_mask = state.protect_mask[keep].reshape(height, width - 1)
    new_col_map = state.col_map[keep].reshape(height, width - 1)
    return CarveState(image=new_image, protect_mask=new_mask, col_map=new_col_map)


def to_original_points(state: CarveState, columns: tuple[int, ...]) -> tuple[tuple[int, int], ...]:
    """Map current-image seam columns to original image coordinates."""
    return tuple(
        (row, int(state.col_map[row, col])) for row, col in enumerate(columns)
    )


def carve_seams(
    image: np.ndarray,
    protect_mask: np.ndarray,
    n_seams: int,
    config: SeamConfig,
    run_logger=None,
    on_chunk: Callable[[dict], None] | None = None,
) -> CarveReport:
    """Remove ``n_seams`` vertical seams, one DP per removal.

    Energy is recomputed from the current image before every removal.
    Progress is reported in chunks of ``config.chunk_size`` seams.
    """
    if n_seams < 1:
        raise ContractViolationError(f"n_seams must be >= 1, got {n_seams}")
    height, width = image.shape[:2]
    if n_seams > width - config.min_width:
        raise ContractViolationError(
            f"cannot remove {n_seams} seams from width {width} "
            f"(min_width={config.min_width})"
        )

    state = CarveState.initial(image, protect_mask)
    seams: list[SeamPath] = []
    chunks: list[dict] = []

    for k in range(n_seams):
        result: SeamResult = find_seam(
            state.image, state.protect_mask, config.energy_mode, run_logger=run_logger
        )
        points = to_original_points(state, result.columns)
        state = remove_seam(state, result.columns)
        seams.append(
            SeamPath(
                points=points,
                energy=result.energy,
                energy_mode=config.energy_mode,
                width_after=state.width,
            )
        )
        done = k + 1
        if done % config.chunk_size == 0 or done == n_seams:
            chunk = {
                "chunk_index": len(chunks),
                "seams_done": done,
                "seams_total": n_seams,
                "current_width": state.width,
                "last_seam_energy": result.energy,
            }
            chunks.append(chunk)
            if run_logger is not None:
                run_logger.emit("chunk_done", step="carve", **chunk)
            if on_chunk is not None:
                on_chunk(chunk)

    return CarveReport(
        seams=tuple(seams),
        energy_mode=config.energy_mode,
        original_shape=(height, width),
        final_width=state.width,
        chunks=tuple(chunks),
    )
