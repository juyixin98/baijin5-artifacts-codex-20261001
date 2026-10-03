"""Pipeline orchestration: contract check -> thin -> validate -> graph.

Produces one explainable report per request: request identity, algorithm and
dependency versions, per-round deletion counts, tile/halo statistics,
topology validation with failure reasons listed separately from uncertain
conclusions (warnings).
"""

from __future__ import annotations

import logging
import time

import numpy as np
import scipy
import PIL

from . import __version__
from . import topology
from .config import Settings, settings
from .contracts import validate_binary_image
from .graph import extract_graph
from .kernel import ALGORITHM_ID, thin
from .tiles import TiledThinner

log = logging.getLogger("skeleton.service")


def validate_topology(original: np.ndarray, skeleton: np.ndarray) -> dict:
    """Compare topology of original vs skeleton with independent probes."""
    checks = {
        "components_equal": topology.count_foreground_components(original)
        == topology.count_foreground_components(skeleton),
        "holes_equal": topology.count_holes(original) == topology.count_holes(skeleton),
        "skeleton_subset_of_original": bool(((skeleton == 1) <= (original == 1)).all()),
        "nonempty_preserved": (int(original.sum()) > 0) == (int(skeleton.sum()) > 0),
    }
    failure_reasons = [name for name, ok in checks.items() if not ok]
    warnings: list[str] = []
    if topology.has_2x2_block(skeleton):
        warnings.append("skeleton contains a 2x2 block; may not be fully thin")
    if int(skeleton.sum()) > 0 and (
        skeleton[0, :].any() or skeleton[-1, :].any()
        or skeleton[:, 0].any() or skeleton[:, -1].any()
    ):
        warnings.append("skeleton touches the image border; endpoint counts near the border are uncertain")
    return {
        "status": "pass" if not failure_reasons else "fail",
        "checks": checks,
        "failure_reasons": failure_reasons,
        "warnings": warnings,
        "metrics": {
            "components_original": topology.count_foreground_components(original),
            "components_skeleton": topology.count_foreground_components(skeleton),
            "holes_original": topology.count_holes(original),
            "holes_skeleton": topology.count_holes(skeleton),
            "endpoints_skeleton": int(topology.endpoint_pixels(skeleton).sum()),
        },
    }


def skeletonize(
    image: np.ndarray,
    *,
    mode: str = "tiled",
    tile_size: int | None = None,
    request_id: str = "-",
    cfg: Settings = settings,
) -> dict:
    started = time.perf_counter()
    img = validate_binary_image(image)
    tile_size = tile_size or cfg.tile_size
    log.info(
        "skeletonize start: shape=%s foreground=%d mode=%s tile_size=%d",
        img.shape, int(img.sum()), mode, tile_size,
    )

    if mode == "tiled":
        result = TiledThinner(
            tile_size=tile_size, max_rounds=cfg.max_rounds, workers=cfg.tile_workers
        ).thin(img)
        tile_info = {
            "tile_size": result.tile_size,
            "tile_count": result.tile_count,
            "halo_exchanges": result.halo_exchanges,
        }
    elif mode == "full":
        result = thin(img, max_rounds=cfg.max_rounds)
        tile_info = None
    else:
        raise ValueError(f"unknown mode {mode!r}; expected 'tiled' or 'full'")

    log.info(
        "thinning done: rounds=%d deleted=%d skeleton_pixels=%d",
        len(result.rounds), result.total_deleted, int(result.skeleton.sum()),
    )

    validation = validate_topology(img, result.skeleton)
    if validation["status"] != "pass":
        log.warning("topology validation FAILED: %s", validation["failure_reasons"])
    graph = extract_graph(result.skeleton)
    log.info(
        "graph extracted: nodes=%d edges=%d", len(graph.nodes), len(graph.edges)
    )

    return {
        "request_id": request_id,
        "versions": {
            "service": __version__,
            "algorithm": ALGORITHM_ID,
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "pillow": PIL.__version__,
        },
        "mode": mode,
        "image_shape": list(img.shape),
        "foreground_pixels_before": int(img.sum()),
        "skeleton_pixels": int(result.skeleton.sum()),
        "total_deleted": result.total_deleted,
        "rounds": [
            {
                "round": r.round_index,
                "sub1_deleted": r.sub1_deleted,
                "sub2_deleted": r.sub2_deleted,
            }
            for r in result.rounds
        ],
        "tiles": tile_info,
        "skeleton": result.skeleton.astype(int).tolist(),
        "graph": graph.to_dict(),
        "validation": validation,
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }
