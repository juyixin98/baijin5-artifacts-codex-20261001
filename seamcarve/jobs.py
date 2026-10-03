"""Chunked carve jobs.

A ``CarveJob`` wraps a multi-seam carve in a run identity: it logs job
start (with input hash and component versions), per-chunk progress and
job completion/failure. Failures propagate with their error category --
a job never reports success after an exception.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .carving import carve_seams
from .config import SeamConfig
from .contracts import CarveReport, image_sha256
from .errors import SeamCarveError
from .runlog import RunLogger, component_versions, new_run_id


class CarveJob:
    def __init__(
        self,
        image: np.ndarray,
        protect_mask: np.ndarray,
        n_seams: int,
        config: SeamConfig,
        job_id: str | None = None,
        log_path: Path | None = None,
    ):
        self.job_id = job_id or new_run_id()
        self.image = image
        self.protect_mask = protect_mask
        self.n_seams = n_seams
        self.config = config
        self.logger = RunLogger(run_id=self.job_id, log_path=log_path)
        self.progress: list[dict] = []

    def run(self) -> CarveReport:
        log = self.logger
        log.emit(
            "job_start",
            step="init",
            job_id=self.job_id,
            image_shape=list(self.image.shape),
            input_sha256=image_sha256(self.image),
            n_seams=self.n_seams,
            energy_mode=self.config.energy_mode,
            chunk_size=self.config.chunk_size,
            versions=component_versions(),
        )
        try:
            report = carve_seams(
                self.image,
                self.protect_mask,
                self.n_seams,
                self.config,
                run_logger=log,
                on_chunk=self.progress.append,
            )
        except SeamCarveError as exc:
            log.emit(
                "job_failed",
                step="carve",
                category=exc.category.value,
                detail=exc.detail,
            )
            raise
        log.emit(
            "job_done",
            step="carve",
            seams_done=len(report.seams),
            final_width=report.final_width,
            total_energy=sum(s.energy for s in report.seams),
        )
        return report
