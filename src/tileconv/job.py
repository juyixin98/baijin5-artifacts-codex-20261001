"""Checkpointed tiled job engine.

A job filters one stored image with one kernel, tile by tile, writing each
tile's valid region into a memmap output. State is persisted after every
tile (atomic tmp-file + rename), so an interrupted job resumes exactly where
it stopped.

Resume binding: before any tile runs, the engine re-computes the input image
digest from the file on disk and the kernel digest from the spec stored in
the job state, and compares both against the digests recorded at creation.
Any deviation raises :class:`DigestMismatchError` — a job never resumes
against silently changed inputs or kernels.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from .contract import BoundaryMode, ImageSpec, KernelSpec, digest_array
from .errors import DigestMismatchError, InjectedInterrupt, JobStateError, NotFoundError
from .kernel import filter_window
from .logging_utils import RunLogger, get_run_logger, versions_snapshot
from .storage import ImageStore
from .tiling import Tile, TileGrid

OUTPUT_DTYPE = np.float64  # accumulation and output are always float64


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class TiledJob:
    def __init__(self, state_path: Path | str, store: ImageStore,
                 run_logger: Optional[RunLogger] = None):
        self.state_path = Path(state_path)
        if not self.state_path.exists():
            raise NotFoundError("job state not found", {"state_path": str(state_path)})
        self.store = store
        self.state: dict[str, Any] = json.loads(self.state_path.read_text())
        self.run_logger = run_logger

    # ------------------------------------------------------------- construction
    @classmethod
    def create(
        cls,
        store: ImageStore,
        jobs_dir: Path | str,
        image: ImageSpec,
        kernel: KernelSpec,
        boundary: BoundaryMode,
        cval: float = 0.0,
        tile_shape: tuple[int, int] = (256, 256),
        logs_dir: Optional[Path | str] = None,
    ) -> "TiledJob":
        job_id = uuid.uuid4().hex[:12]
        run_id = uuid.uuid4().hex[:12]
        jobs_dir = Path(jobs_dir)
        job_dir = jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        grid = TileGrid(image.shape, tile_shape)
        output_path = job_dir / "output.npy"
        out = np.lib.format.open_memmap(output_path, mode="w+",
                                        dtype=OUTPUT_DTYPE, shape=image.shape)
        out.flush()

        state = {
            "job_id": job_id,
            "run_id": run_id,
            "status": "pending",
            "spec": {
                "image_id": image.image_id,
                "image_digest": image.digest,
                "image_shape": list(image.shape),
                "kernel_digest": kernel.digest(),
                "kernel_spec": kernel.to_dict(),
                "boundary": boundary.value,
                "cval": float(cval),
                "tile_shape": [int(tile_shape[0]), int(tile_shape[1])],
                "output_path": str(output_path),
                "output_dtype": str(np.dtype(OUTPUT_DTYPE)),
            },
            "tiles": [dict(t.to_dict(), status="pending") for t in grid],
            "versions": versions_snapshot(),
            "created_at": _utcnow(),
            "updated_at": _utcnow(),
            "error": None,
        }
        state_path = job_dir / "state.json"
        state_path.write_text(json.dumps(state, indent=1))
        run_logger = get_run_logger(logs_dir, run_id) if logs_dir else None
        job = cls(state_path, store, run_logger)
        job._log("job_created", job_id=job_id, image_digest=image.digest,
                 kernel_digest=kernel.digest(), tiles=len(grid),
                 tile_shape=list(tile_shape), boundary=boundary.value,
                 versions=state["versions"])
        return job

    # ------------------------------------------------------------------ helpers
    def _log(self, event: str, level: str = "info", **fields: Any) -> None:
        if self.run_logger is not None:
            self.run_logger.log(event, level=level, **fields)

    def _save_state(self) -> None:
        self.state["updated_at"] = _utcnow()
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state, indent=1))
        os.replace(tmp, self.state_path)

    @property
    def job_id(self) -> str:
        return self.state["job_id"]

    @property
    def status(self) -> str:
        return self.state["status"]

    @property
    def progress(self) -> dict[str, Any]:
        done = sum(1 for t in self.state["tiles"] if t["status"] == "done")
        total = len(self.state["tiles"])
        return {"done": done, "total": total,
                "fraction": (done / total) if total else 1.0}

    def kernel_spec(self) -> KernelSpec:
        return KernelSpec.from_dict(self.state["spec"]["kernel_spec"])

    def output_path(self) -> Path:
        return Path(self.state["spec"]["output_path"])

    def open_output(self) -> np.memmap:
        shape = tuple(self.state["spec"]["image_shape"])
        return np.lib.format.open_memmap(self.output_path(), mode="r+", shape=shape,
                                         dtype=np.dtype(self.state["spec"]["output_dtype"]))

    # ------------------------------------------------------------------ binding
    def verify_bindings(self) -> None:
        """Re-verify input image and kernel digests against the job record."""
        spec = self.state["spec"]
        actual_image = self.store.digest_of(spec["image_id"])
        if actual_image != spec["image_digest"]:
            self._log("digest_mismatch", level="error", what="image",
                      expected=spec["image_digest"], actual=actual_image)
            raise DigestMismatchError(
                "input image digest mismatch; refusing to run/resume",
                {"expected": spec["image_digest"], "actual": actual_image,
                 "image_id": spec["image_id"]},
            )
        actual_kernel = self.kernel_spec().digest()
        if actual_kernel != spec["kernel_digest"]:
            self._log("digest_mismatch", level="error", what="kernel",
                      expected=spec["kernel_digest"], actual=actual_kernel)
            raise DigestMismatchError(
                "kernel digest mismatch; refusing to run/resume",
                {"expected": spec["kernel_digest"], "actual": actual_kernel},
            )
        self._log("digests_verified", image_digest=actual_image,
                  kernel_digest=actual_kernel)

    # ------------------------------------------------------------------ execution
    def run(
        self,
        fail_after: Optional[int] = None,
        on_tile_written: Optional[Callable[[Tile], None]] = None,
    ) -> dict[str, Any]:
        """Run all pending tiles. ``fail_after`` injects a deterministic
        interrupt after that many tiles *of this invocation* (test hook)."""
        if self.status == "completed":
            raise JobStateError("job already completed", {"job_id": self.job_id})
        self.verify_bindings()

        spec = self.state["spec"]
        kernel = self.kernel_spec()
        boundary = BoundaryMode(spec["boundary"])
        cval = float(spec["cval"])
        img = self.store.open_array(spec["image_id"])
        out = self.open_output()

        self.state["status"] = "running"
        self._save_state()
        done_this_run = 0
        try:
            for tile_state in self.state["tiles"]:
                if tile_state["status"] == "done":
                    continue
                tile = Tile.from_dict(tile_state)
                block = filter_window(img, tile.row0, tile.row1, tile.col0, tile.col1,
                                      kernel, boundary, cval)
                out[tile.row0 : tile.row1, tile.col0 : tile.col1] = block
                out.flush()
                tile_state["status"] = "done"
                done_this_run += 1
                prog = self.progress
                self._log("tile_done", tile_id=tile.tile_id,
                          region=[tile.row0, tile.row1, tile.col0, tile.col1],
                          progress=prog)
                self._save_state()
                if on_tile_written is not None:
                    on_tile_written(tile)
                if fail_after is not None and done_this_run >= fail_after:
                    remaining = prog["total"] - prog["done"]
                    if remaining > 0:
                        raise InjectedInterrupt(
                            f"injected interrupt after {done_this_run} tile(s)",
                            {"done_this_run": done_this_run, "remaining": remaining},
                        )
            self.state["status"] = "completed"
            self.state["error"] = None
            self._save_state()
            self._log("job_completed", job_id=self.job_id, progress=self.progress)
        except InjectedInterrupt as exc:
            self.state["status"] = "interrupted"
            self.state["error"] = exc.to_dict()
            self._save_state()
            self._log("job_interrupted", level="warning", job_id=self.job_id,
                      progress=self.progress, error=exc.to_dict())
            raise
        except Exception as exc:
            self.state["status"] = "failed"
            self.state["error"] = {"category": type(exc).__name__, "message": str(exc)}
            self._save_state()
            self._log("job_failed", level="error", job_id=self.job_id,
                      error=self.state["error"])
            raise
        return {"job_id": self.job_id, "status": self.state["status"],
                "progress": self.progress}

    def resume(self, **kwargs: Any) -> dict[str, Any]:
        """Resume an interrupted/failed job after re-verifying digests."""
        if self.status == "completed":
            raise JobStateError("job already completed", {"job_id": self.job_id})
        if self.status not in ("interrupted", "failed", "pending", "running"):
            raise JobStateError("unknown job status; refusing to resume",
                                {"job_id": self.job_id, "status": self.status})
        self._log("job_resuming", job_id=self.job_id, progress=self.progress)
        return self.run(**kwargs)
