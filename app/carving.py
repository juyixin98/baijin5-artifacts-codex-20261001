"""Seam removal orchestration.

The carver owns the *current* image plus a per-row column map back to
original-image coordinates.  After every removal:

* the energy surface is recomputed from the current image (never reused
  from the original), and
* the column map is rewritten so reported paths are always in original
  image coordinates.

Both invariants are directly asserted by the test-suite.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from app.config import Settings
from app.errors import InvalidRequestError
from app.kernel import SeamResult, find_seam
from app.logging_setup import get_logger, log_event


@dataclass(frozen=True)
class SeamRecord:
    """One removed seam, reported in original-image coordinates."""

    index: int
    path_original_cols: tuple[int, ...]
    energy: float
    mode: str
    width_before: int
    width_after: int


@dataclass
class CarveResult:
    seams: list[SeamRecord] = field(default_factory=list)
    final_image: np.ndarray | None = None
    original_shape: tuple[int, ...] = ()

    @property
    def removed(self) -> int:
        return len(self.seams)


class Carver:
    """Stateful carver: removes seams one at a time, recomputing energy."""

    def __init__(
        self,
        image: np.ndarray,
        protected_mask: np.ndarray | None,
        settings: Settings,
        *,
        run_id: str = "",
        input_sha256: str = "",
        logger: logging.Logger | None = None,
    ) -> None:
        arr = np.asarray(image)
        if arr.ndim not in (2, 3) or arr.shape[0] < 1 or arr.shape[1] < 1:
            raise InvalidRequestError(
                "image must be a non-empty 2-D or 3-D array",
                details={"shape": list(arr.shape)},
            )
        self._settings = settings
        self._current = arr.copy()
        if protected_mask is None:
            self._mask = np.zeros(arr.shape[:2], dtype=bool)
        else:
            mask = np.asarray(protected_mask, dtype=bool)
            if mask.shape != arr.shape[:2]:
                raise InvalidRequestError(
                    "protected mask shape must match the image",
                    details={
                        "image_shape": list(arr.shape[:2]),
                        "mask_shape": list(mask.shape),
                    },
                )
            self._mask = mask.copy()
        # col_map[r, c] = original-image column of current pixel (r, c).
        self._col_map = np.tile(
            np.arange(arr.shape[1], dtype=np.int64), (arr.shape[0], 1)
        )
        self._original_shape = tuple(int(d) for d in arr.shape)
        self._run_id = run_id
        self._input_sha256 = input_sha256
        self._logger = logger or get_logger()
        self._records: list[SeamRecord] = []

    @property
    def current_image(self) -> np.ndarray:
        return self._current

    @property
    def current_width(self) -> int:
        return int(self._current.shape[1])

    @property
    def records(self) -> list[SeamRecord]:
        return list(self._records)

    def remove_one(self) -> SeamRecord:
        width = self.current_width
        if width <= self._settings.min_remaining_width:
            raise InvalidRequestError(
                "cannot remove a seam: minimum remaining width reached",
                details={
                    "current_width": width,
                    "min_remaining_width": self._settings.min_remaining_width,
                },
            )
        # Energy is recomputed from the *current* image on every removal.
        seam: SeamResult = find_seam(self._current, self._mask, self._settings)
        height = self._current.shape[0]
        rows = np.arange(height)
        path = np.asarray(seam.path, dtype=np.int64)
        original_cols = tuple(int(self._col_map[r, path[r]]) for r in rows)

        keep = np.ones((height, width), dtype=bool)
        keep[rows, path] = False
        self._current = self._current[keep].reshape(
            (height, width - 1) + self._current.shape[2:]
        )
        self._mask = self._mask[keep].reshape(height, width - 1)
        self._col_map = self._col_map[keep].reshape(height, width - 1)

        record = SeamRecord(
            index=len(self._records),
            path_original_cols=original_cols,
            energy=seam.energy,
            mode=seam.mode,
            width_before=width,
            width_after=width - 1,
        )
        self._records.append(record)
        log_event(
            self._logger,
            logging.INFO,
            "seam_selected",
            f"seam {record.index} removed: energy={seam.energy:.6f} "
            f"start_col={seam.start_col} width {width}->{width - 1}",
            run_id=self._run_id,
            input_sha256=self._input_sha256,
            seam_index=record.index,
            energy=seam.energy,
            energy_mode=seam.mode,
            max_displacement=seam.max_displacement,
            start_col=seam.start_col,
            final_row_ties=seam.final_row_ties,
            width_before=width,
            width_after=width - 1,
        )
        return record

    def remove_many(self, count: int) -> CarveResult:
        if count < 1:
            raise InvalidRequestError(
                "num_seams must be >= 1", details={"num_seams": count}
            )
        if count > self.current_width - self._settings.min_remaining_width:
            raise InvalidRequestError(
                "num_seams exceeds removable width",
                details={
                    "num_seams": count,
                    "current_width": self.current_width,
                    "min_remaining_width": self._settings.min_remaining_width,
                },
            )
        for _ in range(count):
            self.remove_one()
        return CarveResult(
            seams=list(self._records),
            final_image=self._current,
            original_shape=self._original_shape,
        )
