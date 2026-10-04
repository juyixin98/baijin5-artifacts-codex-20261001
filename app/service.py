"""Service orchestration: validation, execution path, stats, logging.

This layer is transport-agnostic (the HTTP layer is a thin wrapper), which
makes it directly unit-testable.
"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field

import numpy as np

from . import KERNEL_VERSION, __version__
from .config import Settings, settings as default_settings
from .io_image import ImageDecodeError, decode_mask_b64
from .kernel import EDTResult
from .tiling import TilingReport, tiled_edt


class ServiceError(Exception):
    def __init__(self, code: str, message: str, location: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.location = location


def _configure_logger() -> logging.Logger:
    logger = logging.getLogger("edt-service")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(default_settings.log_level)
    return logger


logger = _configure_logger()


def _log(level: int, request_id: str, event: str, **fields) -> None:
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "service": "edt-service",
        "version": __version__,
        "kernel_version": KERNEL_VERSION,
        "request_id": request_id,
        "event": event,
        **fields,
    }
    logger.log(level, json.dumps(payload, default=str))


@dataclass
class ServiceOutput:
    mask: np.ndarray
    result: EDTResult
    tiling: TilingReport | None
    request_id: str
    elapsed_ms: float
    warnings: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)


def _validate_limits(
    mask: np.ndarray, spacing_y: float, spacing_x: float, cfg: Settings
) -> None:
    h, w = mask.shape
    if h > cfg.max_edge_px or w > cfg.max_edge_px:
        raise ServiceError(
            "TOO_LARGE",
            f"raster edge ({h}x{w}) exceeds limit {cfg.max_edge_px} px",
            "image.shape",
        )
    if h * w > cfg.max_total_cells:
        raise ServiceError(
            "TOO_LARGE",
            f"raster has {h * w} cells; limit is {cfg.max_total_cells}",
            "image.shape",
        )
    for name, value in (("spacing_y", spacing_y), ("spacing_x", spacing_x)):
        if not (cfg.min_spacing <= value <= cfg.max_spacing):
            raise ServiceError(
                "INVALID_SPACING",
                f"{name}={value} outside allowed range "
                f"[{cfg.min_spacing}, {cfg.max_spacing}]",
                f"body.{name}",
            )


def run_edt(
    image_base64: str,
    spacing_y: float,
    spacing_x: float,
    source_value: str,
    force_tiled: bool,
    request_id: str | None,
    cfg: Settings | None = None,
) -> ServiceOutput:
    cfg = cfg or default_settings
    rid = request_id or f"req-{uuid.uuid4().hex[:12]}"
    t0 = time.perf_counter()
    steps: list[dict] = []

    def step(name: str, t_start: float) -> float:
        now = time.perf_counter()
        steps.append({"name": name, "elapsed_ms": round((now - t_start) * 1000, 3)})
        return now

    _log(logging.INFO, rid, "request_received",
         spacing_y=spacing_y, spacing_x=spacing_x, force_tiled=force_tiled)

    try:
        mask = decode_mask_b64(image_base64, source_value=source_value)
    except ImageDecodeError as exc:
        _log(logging.WARNING, rid, "decode_failed", reason=str(exc))
        raise ServiceError("INVALID_IMAGE", str(exc), "body.image_base64") from exc

    t = step("decode", t0)
    _validate_limits(mask, spacing_y, spacing_x, cfg)
    t = step("validate", t)

    h, w = mask.shape
    threshold = cfg.tile_threshold_cells
    use_tiles = force_tiled or h * w > threshold
    if use_tiles:
        result, report = tiled_edt(
            mask, spacing_y, spacing_x, cfg.tile_target_cells, force=True
        )
        _log(
            logging.INFO, rid, "edt_tiled",
            rows=h, columns=w, tiles=len(report.windows),
            tiling_reason=report.reason,
            manhattan_bound=report.manhattan_max,
        )
    else:
        from .kernel import edt as kernel_edt
        result = kernel_edt(mask, spacing_y, spacing_x)
        report = None
        _log(logging.INFO, rid, "edt_direct", rows=h, columns=w)
    t = step("edt", t)

    warnings: list[dict] = []
    tie_count = int(result.ties.sum()) if result.has_sources else 0
    if tie_count:
        warnings.append({
            "code": "EQUI_DISTANT_TIE",
            "message": (
                f"{tie_count} pixel(s) have two or more sources equidistant "
                "within rtol=1e-12; the lexicographically smallest "
                "(row, column) source was selected"
            ),
        })
    if result.has_sources and not np.isfinite(result.distances).all():
        warnings.append({
            "code": "NON_FINITE_DISTANCE",
            "message": "some distances are non-finite despite sources existing",
        })
    finite = np.isfinite(result.distances)
    if finite.any() and float(result.distances[finite].max()) > cfg.distance_warn_ratio:
        warnings.append({
            "code": "DISTANCE_SCALE",
            "message": "maximum distance is very large; verify spacing units",
        })

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 3)
    _log(
        logging.INFO, rid, "request_completed",
        elapsed_ms=elapsed_ms,
        path="tiled" if use_tiles else "direct",
        has_sources=result.has_sources,
        tie_pixels=tie_count,
        warnings=[w["code"] for w in warnings],
    )
    return ServiceOutput(
        mask=mask,
        result=result,
        tiling=report,
        request_id=rid,
        elapsed_ms=elapsed_ms,
        warnings=warnings,
        steps=steps,
    )


def stats_from_result(result: EDTResult) -> dict:
    h, w = result.distances.shape
    finite = np.isfinite(result.distances)
    # For an exact Euclidean EDT the zero-distance pixels are exactly the
    # source pixels.
    source_count = int(np.count_nonzero(result.distances == 0.0))
    if finite.any():
        vals = result.distances[finite]
        return {
            "rows": h,
            "columns": w,
            "source_count": source_count,
            "min_distance": float(vals.min()),
            "max_distance": float(vals.max()),
            "mean_distance": float(vals.mean()),
            "has_sources": True,
            "all_sources": bool(source_count == h * w),
        }
    return {
        "rows": h,
        "columns": w,
        "source_count": 0,
        "min_distance": None,
        "max_distance": None,
        "mean_distance": None,
        "has_sources": False,
        "all_sources": False,
    }
