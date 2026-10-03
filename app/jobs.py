"""Tiled job orchestration with checkpointing and digest-bound recovery.

A job is fully described by a :class:`JobSpec` (image descriptor, kernel,
boundary mode, tile size).  ``create`` freezes the input and kernel digests
into a manifest; every subsequent ``execute``/``resume`` re-derives the
digests from the spec and refuses to continue on mismatch, so a recovered
run is provably bound to the same input and kernel it started with.

Tiles are processed in index order; after each tile the output memmap is
flushed and the checkpoint file is atomically replaced, so an interrupt at
any point leaves a consistent, resumable state.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import fixtures
from .config import Settings, get_settings
from .contract import (
    BoundaryMode,
    ContractError,
    ImageDocument,
    KernelSpec,
    SeparableKernelSpec,
)
from .kernels import filter_separable_window, filter_window
from .logging_utils import RunLogger, versions
from .memory import peak_rss_bytes
from .tiling import Tile, plan_tiles, validate_tiling


# ------------------------------------------------------------------ errors
class JobError(Exception):
    """Base class for job failures; ``category`` is a stable machine tag."""

    category = "job_error"


class UnknownJobError(JobError):
    category = "unknown_job"


class DigestMismatchError(JobError):
    category = "digest_mismatch"


class InvalidSpecError(JobError):
    category = "invalid_spec"


class JobStateError(JobError):
    category = "job_state"


# ------------------------------------------------------------------ spec
def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def build_image(desc: dict[str, Any], settings: Settings) -> ImageDocument:
    """Materialise an image from its fixture descriptor."""
    kind = desc.get("kind")
    shape = tuple(desc.get("shape", ()))
    if kind != "png":
        if len(shape) != 2 or shape[0] < 1 or shape[1] < 1:
            raise InvalidSpecError(f"image descriptor needs shape [H, W], got {desc!r}")
        if shape[0] * shape[1] > settings.max_image_pixels:
            raise InvalidSpecError(
                f"image {shape} exceeds max_image_pixels={settings.max_image_pixels}"
            )
    if kind == "impulse":
        pos = tuple(desc["pos"]) if "pos" in desc else None
        data = fixtures.impulse_image(shape, pos=pos, value=float(desc.get("value", 1.0)))
    elif kind == "step_edge":
        data = fixtures.step_edge_image(
            shape,
            axis=int(desc.get("axis", 1)),
            loc=desc.get("loc"),
            low=float(desc.get("low", 0.0)),
            high=float(desc.get("high", 1.0)),
        )
    elif kind == "tagged_border":
        data = fixtures.tagged_border_image(
            shape,
            border=int(desc.get("border", 1)),
            border_values=tuple(desc.get("border_values", (1.0, 2.0, 3.0, 4.0))),
        )
    elif kind == "noise":
        data = fixtures.seeded_noise_image(shape, seed=int(desc.get("seed", 0)))
    elif kind == "png":
        path = Path(desc["path"])
        if not path.is_file():
            raise InvalidSpecError(f"png fixture not found: {path}")
        data = fixtures.load_png(path)
    else:
        raise InvalidSpecError(f"unknown image kind: {kind!r}")
    return ImageDocument(data=data, source=_canonical(desc))


def build_kernel(
    desc: dict[str, Any], settings: Settings
) -> KernelSpec | SeparableKernelSpec:
    kind = desc.get("kind")
    try:
        if kind == "dense":
            spec: KernelSpec | SeparableKernelSpec = KernelSpec.from_array(
                desc["weights"], anchor=tuple(desc["anchor"]) if "anchor" in desc else None
            )
        elif kind == "separable":
            spec = SeparableKernelSpec.from_vectors(
                desc["col"], desc["row"],
                anchor=tuple(desc["anchor"]) if "anchor" in desc else None,
            )
        elif kind == "box":
            spec = fixtures.box_kernel(int(desc["k"]))
        elif kind == "gaussian":
            spec = fixtures.gaussian_kernel(int(desc["k"]), float(desc["sigma"]))
        elif kind == "separable_gaussian":
            spec = fixtures.separable_gaussian(int(desc["k"]), float(desc["sigma"]))
        else:
            raise InvalidSpecError(f"unknown kernel kind: {kind!r}")
    except ContractError as exc:
        raise InvalidSpecError(str(exc)) from exc
    ky, kx = spec.shape
    if ky > settings.max_kernel_size or kx > settings.max_kernel_size:
        raise InvalidSpecError(
            f"kernel {spec.shape} exceeds max_kernel_size={settings.max_kernel_size}"
        )
    return spec


@dataclass
class JobSpec:
    image: dict[str, Any]
    kernel: dict[str, Any]
    boundary: str = "mirror"
    cval: float = 0.0
    tile: tuple[int, int] = (512, 512)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "JobSpec":
        try:
            tile = d.get("tile", [512, 512])
            return cls(
                image=d["image"],
                kernel=d["kernel"],
                boundary=d.get("boundary", "mirror"),
                cval=float(d.get("cval", 0.0)),
                tile=(int(tile[0]), int(tile[1])),
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise InvalidSpecError(f"malformed job spec: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "image": self.image,
            "kernel": self.kernel,
            "boundary": self.boundary,
            "cval": self.cval,
            "tile": list(self.tile),
        }


# ------------------------------------------------------------------ runner
MANIFEST = "manifest.json"
CHECKPOINT = "checkpoint.json"
OUTPUT = "output.npy"
REPORT = "report.json"


def _atomic_write_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _file_digest(path: Path, chunk_size: int = 1 << 20) -> str:
    """Streamed sha256 of a file — never loads it into memory at once."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class TiledJobRunner:
    spec: JobSpec
    settings: Settings = field(default_factory=get_settings)
    # When loaded from an existing manifest, the runner is pinned to that
    # job directory even if the (possibly tampered) spec would hash to a
    # different id — that is exactly what digest verification must catch.
    job_id_override: str | None = None

    # -- identity ------------------------------------------------------
    def _resolved(self) -> tuple[ImageDocument, KernelSpec | SeparableKernelSpec, BoundaryMode]:
        image = build_image(self.spec.image, self.settings)
        kernel = build_kernel(self.spec.kernel, self.settings)
        try:
            boundary = BoundaryMode.parse(self.spec.boundary)
        except ContractError as exc:
            raise InvalidSpecError(str(exc)) from exc
        return image, kernel, boundary

    def compute_job_id(self) -> str:
        image, kernel, _ = self._resolved()
        blob = _canonical(self.spec.to_dict()) + image.digest() + kernel.digest()
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    @property
    def job_dir(self) -> Path:
        job_id = self.job_id_override or self.compute_job_id()
        return self.settings.workspace_dir / "jobs" / job_id

    # -- lifecycle -----------------------------------------------------
    def create(self) -> dict[str, Any]:
        """Freeze input/kernel digests and tile plan into a manifest."""
        if self.job_id_override is not None:
            raise JobStateError("runner is pinned to an existing job; cannot create")
        image, kernel, boundary = self._resolved()
        tiles = plan_tiles(image.shape, self.spec.tile)
        validate_tiling(tiles, image.shape)
        job_id = self.compute_job_id()
        job_dir = self.job_dir
        if (job_dir / MANIFEST).exists():
            raise JobStateError(f"job {job_id} already exists; use resume")
        job_dir.mkdir(parents=True)
        manifest = {
            "job_id": job_id,
            "status": "pending",
            "spec": self.spec.to_dict(),
            "boundary": boundary.value,
            "shape": list(image.shape),
            "input_digest": image.digest(),
            "kernel_digest": kernel.digest(),
            "kernel_kind": "separable" if isinstance(kernel, SeparableKernelSpec) else "dense",
            "tiles": [t.__dict__ for t in tiles],
            "versions": versions(),
            "created_at": time.time(),
        }
        _atomic_write_json(job_dir / MANIFEST, manifest)
        _atomic_write_json(job_dir / CHECKPOINT, {"done": [], "updated_at": time.time()})
        # Pre-allocate the output so tile writes are in-place slices.
        out = np.lib.format.open_memmap(
            job_dir / OUTPUT, mode="w+", dtype=np.float64, shape=image.shape
        )
        out[:] = np.nan  # unwritten pixels are detectable, never silently zero
        del out
        return manifest

    def _load_manifest(self) -> dict[str, Any]:
        path = self.job_dir / MANIFEST
        if not path.exists():
            raise UnknownJobError(f"no job at {self.job_dir}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _verify_digests(
        self,
        manifest: dict[str, Any],
        image: ImageDocument,
        kernel: KernelSpec | SeparableKernelSpec,
    ) -> None:
        if image.digest() != manifest["input_digest"]:
            raise DigestMismatchError(
                "input image digest differs from manifest; refusing to resume "
                "against a different input"
            )
        if kernel.digest() != manifest["kernel_digest"]:
            raise DigestMismatchError(
                "kernel digest differs from manifest; refusing to resume "
                "against a different kernel"
            )

    def _process_pending_tiles(
        self,
        manifest: dict[str, Any],
        image: ImageDocument,
        kernel: KernelSpec | SeparableKernelSpec,
        boundary: BoundaryMode,
        tiles: list[Tile],
        done: set[int],
        max_tiles: int | None,
        logger: RunLogger,
    ) -> None:
        """Process pending tiles in order, checkpointing after each one."""
        checkpoint_path = self.job_dir / CHECKPOINT
        out = np.lib.format.open_memmap(self.job_dir / OUTPUT, mode="r+")
        processed = 0
        try:
            for tile in (t for t in tiles if t.index not in done):
                if max_tiles is not None and processed >= max_tiles:
                    break
                t0 = time.perf_counter()
                if manifest["kernel_kind"] == "separable":
                    block = filter_separable_window(
                        image.data, kernel, (tile.y0, tile.x0), (tile.h, tile.w),
                        boundary, self.spec.cval,
                    )
                else:
                    block = filter_window(
                        image.data, kernel, (tile.y0, tile.x0), (tile.h, tile.w),
                        boundary, self.spec.cval,
                    )
                # Each tile writes exactly its own window: no valid pixel of
                # another tile is touched (guaranteed by validate_tiling).
                out[tile.y0 : tile.y0 + tile.h, tile.x0 : tile.x0 + tile.w] = block
                out.flush()
                done.add(tile.index)
                processed += 1
                _atomic_write_json(
                    checkpoint_path, {"done": sorted(done), "updated_at": time.time()}
                )
                logger.log(
                    "tile_done",
                    tile=tile.index,
                    origin=[tile.y0, tile.x0],
                    extent=[tile.h, tile.w],
                    elapsed_ms=round((time.perf_counter() - t0) * 1e3, 3),
                    progress=f"{len(done)}/{len(tiles)}",
                )
        finally:
            del out

    def execute(self, max_tiles: int | None = None) -> dict[str, Any]:
        """Process pending tiles; stops early (INTERRUPTED) after max_tiles."""
        manifest = self._load_manifest()
        if manifest["status"] == "completed":
            return self._status_payload(manifest, "already completed")
        image, kernel, boundary = self._resolved()
        self._verify_digests(manifest, image, kernel)

        job_id = manifest["job_id"]
        checkpoint = json.loads(
            (self.job_dir / CHECKPOINT).read_text(encoding="utf-8")
        )
        done: set[int] = set(checkpoint["done"])
        tiles = [Tile(**t) for t in manifest["tiles"]]

        logger = RunLogger(job_id, log_dir=self.settings.log_dir)
        logger.log(
            "execute_start",
            pending=len(tiles) - len(done),
            done=len(done),
            total=len(tiles),
            input_digest=manifest["input_digest"],
            kernel_digest=manifest["kernel_digest"],
        )
        self._process_pending_tiles(
            manifest, image, kernel, boundary, tiles, done, max_tiles, logger
        )

        remaining = len(tiles) - len(done)
        if remaining > 0:
            manifest["status"] = "interrupted"
            _atomic_write_json(self.job_dir / MANIFEST, manifest)
            logger.log("execute_interrupted", remaining=remaining)
            logger.close()
            return self._status_payload(manifest, f"interrupted with {remaining} tiles left")

        manifest["status"] = "completed"
        manifest["completed_at"] = time.time()
        _atomic_write_json(self.job_dir / MANIFEST, manifest)
        report = {
            "job_id": job_id,
            "status": "completed",
            "tiles_total": len(tiles),
            "output_digest": _file_digest(self.job_dir / OUTPUT),
            "peak_rss_bytes": peak_rss_bytes(),
            "versions": versions(),
            "finished_at": time.time(),
        }
        _atomic_write_json(self.job_dir / REPORT, report)
        logger.log(
            "execute_completed",
            output_digest=report["output_digest"],
            peak_rss_bytes=report["peak_rss_bytes"],
        )
        logger.close()
        return self._status_payload(manifest, "completed")

    def _status_payload(self, manifest: dict[str, Any], message: str) -> dict[str, Any]:
        checkpoint_path = self.job_dir / CHECKPOINT
        done = 0
        if checkpoint_path.exists():
            done = len(json.loads(checkpoint_path.read_text(encoding="utf-8"))["done"])
        total = len(manifest["tiles"])
        return {
            "job_id": manifest["job_id"],
            "status": manifest["status"],
            "message": message,
            "progress": {"done": done, "total": total},
        }

    def result_array(self) -> np.ndarray:
        manifest = self._load_manifest()
        if manifest["status"] != "completed":
            raise JobStateError(
                f"job {manifest['job_id']} is {manifest['status']}, not completed"
            )
        return np.load(self.job_dir / OUTPUT)

    def report(self) -> dict[str, Any]:
        path = self.job_dir / REPORT
        if not path.exists():
            raise JobStateError("job has no completion report")
        return json.loads(path.read_text(encoding="utf-8"))


def load_runner(job_id: str, settings: Settings | None = None) -> TiledJobRunner:
    """Reconstruct a runner from a persisted manifest (used by resume)."""
    settings = settings or get_settings()
    path = settings.workspace_dir / "jobs" / job_id / MANIFEST
    if not path.exists():
        raise UnknownJobError(f"unknown job id: {job_id}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return TiledJobRunner(
        spec=JobSpec.from_dict(manifest["spec"]),
        settings=settings,
        job_id_override=job_id,
    )
