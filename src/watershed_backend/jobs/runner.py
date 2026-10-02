"""Chunked job runner: decomposes one segmentation request into stages.

A job is executed as the fixed stage sequence ``validate -> flood ->
extract_boundary -> finalize``.  The flood stage itself reports progress in
pixel chunks (``settings.progress_chunk_pixels``) so long runs are
observable.  Any exception moves the job to ``FAILED`` with a typed error
category; the runner never converts a failure into a success response.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np

from ..config import Settings
from ..contracts import (
    SeedPoint,
    build_markers_from_seeds,
    validate_segmentation_input,
)
from ..errors import KernelError
from ..kernel import flood_watershed
from ..logging_utils import input_fingerprint
from ..version import runtime_versions
from .store import (
    COMPLETED,
    FAILED,
    RUNNING,
    JobRecord,
    JobStore,
    StageRecord,
)

ALGORITHM_DESCRIPTION = {
    "method": "meyer_immersion",
    "tie_break": "(elevation, insertion_counter); seeds row-major, neighbours fixed offset order",
    "boundary_rule": "pixel with >= 2 distinct labelled neighbours becomes ridge (label 0), never propagates",
    "unreached_rule": "active pixels unreachable from any seed become label -1 and are counted",
}


class JobRunner:
    def __init__(self, store: JobStore, settings: Settings, logger) -> None:
        self.store = store
        self.settings = settings
        self.logger = logger

    # -- public API ------------------------------------------------------

    def submit(self, request: dict) -> JobRecord:
        record = self.store.create(request)
        self._log(record, "job_submitted", stage=None, detail=None)
        return record

    def run(self, job_id: str) -> JobRecord:
        record = self.store.get(job_id)
        if record is None:
            raise KeyError(f"unknown job {job_id}")
        record.status = RUNNING
        try:
            self._execute(record)
        except KernelError as exc:
            record.status = FAILED
            record.error = exc.to_dict()
            self._log(record, "job_failed", stage=None, detail=record.error)
        except Exception as exc:  # never report unknown states as success
            record.status = FAILED
            record.error = {"category": "INTERNAL", "message": f"{type(exc).__name__}: {exc}"}
            self._log(record, "job_failed", stage=None, detail=record.error)
        else:
            record.status = COMPLETED
            self._log(record, "job_completed", stage=None,
                      detail={"stats": record.result["stats"] if record.result else None})
        return record

    # -- internals -------------------------------------------------------

    def _execute(self, record: JobRecord) -> None:
        request = record.request

        seg_input = self._stage(record, "validate", lambda: self._validate(request))
        record.input_sha256 = input_fingerprint(
            seg_input.gradient, seg_input.markers, seg_input.connectivity, seg_input.mask
        )
        self._log(record, "input_validated", stage="validate", detail={
            "shape": list(seg_input.shape),
            "seed_count": seg_input.seed_count,
            "marker_labels": list(seg_input.marker_labels),
            "connectivity": seg_input.connectivity,
            "versions": runtime_versions(),
        })

        def on_progress(done: int, total: int, elevation: float) -> None:
            record.progress.append(
                {"stage": "flood", "done_pixels": done,
                 "total_pixels": total, "water_level": elevation}
            )

        result = self._stage(
            record, "flood",
            lambda: flood_watershed(
                seg_input.gradient,
                seg_input.markers,
                connectivity=seg_input.connectivity,
                mask=seg_input.mask,
                progress_callback=on_progress,
                progress_chunk=self.settings.progress_chunk_pixels,
            ),
        )
        self._log(record, "flood_finished", stage="flood", detail={
            "popped_pixels": result.stats.popped_pixels,
            "distinct_levels": result.stats.distinct_levels,
            "boundary_pixels": result.stats.boundary_pixels,
            "unreached_pixels": result.stats.unreached_pixels,
        })

        boundary_image = self._stage(
            record, "extract_boundary",
            lambda: (result.boundary.astype(np.uint8) * 255),
        )

        def finalize() -> dict:
            stats = result.stats
            return {
                "shape": list(seg_input.shape),
                "labels": result.labels.tolist(),
                "boundary": result.boundary.tolist(),
                "boundary_image": boundary_image.tolist(),
                "stats": {
                    "seed_pixels": stats.seed_pixels,
                    "assigned_pixels": stats.assigned_pixels,
                    "boundary_pixels": stats.boundary_pixels,
                    "unreached_pixels": stats.unreached_pixels,
                    "distinct_levels": stats.distinct_levels,
                    "min_elevation": stats.min_elevation,
                    "max_elevation": stats.max_elevation,
                    "label_counts": {str(k): v for k, v in stats.label_counts.items()},
                    "connectivity": stats.connectivity,
                },
                "warnings": (
                    ["UNREACHED_PIXELS: active pixels without any seed in their "
                     "mask-connected component were labelled -1"]
                    if stats.unreached_pixels else []
                ),
                "algorithm": ALGORITHM_DESCRIPTION,
                "versions": runtime_versions(),
            }

        record.result = self._stage(record, "finalize", finalize)

    def _validate(self, request: dict):
        gradient = np.asarray(request["gradient"], dtype=np.float64)
        if request.get("markers") is not None:
            markers = np.asarray(request["markers"], dtype=np.int32)
        else:
            seeds = [SeedPoint(int(s["row"]), int(s["col"]), int(s["label"]))
                     for s in request["seeds"]]
            markers = build_markers_from_seeds(seeds, gradient.shape)
        mask = None if request.get("mask") is None else np.asarray(request["mask"], dtype=bool)
        connectivity = int(request.get("connectivity") or self.settings.default_connectivity)
        return validate_segmentation_input(gradient, markers, connectivity, mask, self.settings)

    def _stage(self, record: JobRecord, name: str, fn: Callable[[], Any]) -> Any:
        stage = StageRecord(name=name, started_at=datetime.now(timezone.utc).isoformat())
        record.stages.append(stage)
        started = time.perf_counter()
        output = fn()
        stage.duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return output

    def _log(self, record: JobRecord, event: str, stage: str | None, detail: Any) -> None:
        self.logger.info(
            event,
            extra={
                "job_id": record.job_id,
                "input_sha256": record.input_sha256,
                "stage": stage,
                "event": event,
                "detail": detail,
            },
        )
