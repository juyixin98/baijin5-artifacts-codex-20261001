"""Chunked job execution: batch pairs and tiled estimation for large images.

Two real responsibilities live here:

* ``run_batch`` executes a list of estimation jobs, isolating failures per job
  so one bad pair cannot abort the batch.
* ``estimate_tiled`` splits a large image pair into overlapping tiles, runs the
  kernel per tile, and aggregates the surviving estimates with a confidence
  weighted median.  Per-tile failures (flat tiles, ambiguous tiles) are
  reported, not hidden.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

import numpy as np

from .config import KernelConfig, DEFAULT_CONFIG
from .kernel import estimate_translation
from .kernel.types import EstimateStatus


@dataclass
class Job:
    ref: np.ndarray
    mov: np.ndarray
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    config: KernelConfig = DEFAULT_CONFIG


@dataclass
class JobResult:
    job_id: str
    ok: bool
    result: dict | None = None
    error: str | None = None
    duration_ms: float = 0.0


def run_batch(jobs: list[Job]) -> list[JobResult]:
    """Run estimation jobs sequentially (deterministic), isolating errors."""
    results: list[JobResult] = []
    for job in jobs:
        t0 = time.perf_counter()
        try:
            res = estimate_translation(job.ref, job.mov, job.config)
            results.append(JobResult(
                job_id=job.job_id,
                ok=res.status is not EstimateStatus.FAILED,
                result=res.to_dict(),
                duration_ms=round((time.perf_counter() - t0) * 1e3, 3),
            ))
        except Exception as exc:  # per-job isolation: report, don't abort batch
            results.append(JobResult(
                job_id=job.job_id,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=round((time.perf_counter() - t0) * 1e3, 3),
            ))
    return results


def _tile_origins(length: int, tile: int, stride: int) -> list[int]:
    origins = list(range(0, max(1, length - tile + 1), stride))
    last = length - tile
    if origins and origins[-1] != last and last > 0:
        origins.append(last)
    return origins or [0]


def estimate_tiled(ref: np.ndarray, mov: np.ndarray,
                   tile_size: int = 64, stride: int = 32,
                   config: KernelConfig = DEFAULT_CONFIG) -> dict:
    """Aggregate per-tile estimates into one robust shift for large images."""
    h, w = ref.shape
    tile_jobs: list[tuple[tuple[int, int], Job]] = []
    for oy in _tile_origins(h, tile_size, stride):
        for ox in _tile_origins(w, tile_size, stride):
            sl = (slice(oy, oy + tile_size), slice(ox, ox + tile_size))
            tile_jobs.append(((oy, ox), Job(ref=ref[sl], mov=mov[sl],
                                            job_id=f"tile-{oy}-{ox}", config=config)))
    tile_results = run_batch([job for _, job in tile_jobs])

    usable: list[tuple[tuple[float, float], float]] = []  # (shift, weight)
    per_tile: list[dict] = []
    for (origin, _), res in zip(tile_jobs, tile_results):
        entry = {"origin": list(origin), "ok": res.ok}
        if res.ok and res.result and res.result["status"] == "ok":
            shift = res.result["shift"]
            weight = max(res.result["confidence"].get("peak_to_sidelobe", 1.0), 1e-6)
            usable.append(((shift["dy"], shift["dx"]), weight))
            entry["shift"] = shift
        elif res.result:
            entry["status"] = res.result["status"]
            entry["failure_reason"] = res.result["failure_reason"]
        else:
            entry["error"] = res.error
        per_tile.append(entry)

    if not usable:
        return {"status": "failed", "failure_reason": "no_usable_tiles",
                    "tiles": per_tile}

    shifts = np.array([s for s, _ in usable])
    weights = np.array([wt for _, wt in usable])

    def weighted_median(values: np.ndarray, wts: np.ndarray) -> float:
        order = np.argsort(values)
        v, wt = values[order], wts[order]
        cum = np.cumsum(wt) - 0.5 * wt
        cum /= cum[-1] + 0.5 * wt[-1]
        return float(np.interp(0.5, cum, v))

    dy = weighted_median(shifts[:, 0], weights)
    dx = weighted_median(shifts[:, 1], weights)
    spread = float(np.median(np.abs(shifts - np.array([dy, dx])).max(axis=1).max()
                             if len(shifts) > 1 else 0.0))
    return {
        "status": "ok" if len(usable) >= 2 else "uncertain",
        "shift": {"dy": dy, "dx": dx},
        "n_tiles": len(tile_jobs),
        "n_usable_tiles": len(usable),
        "tile_spread_px": spread,
        "tiles": per_tile,
    }
