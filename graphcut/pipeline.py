"""Synchronous solve pipeline: spec -> graph -> max-flow -> certificate."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable

from .config import AppConfig
from .errors import ComputationError
from .graph import build_graph
from .logging_utils import get_logger, log_event
from .models import EnergySpec, SolveResult
from .solver import solve_maxflow
from .verify import build_certificate, make_result


def run_pipeline(
    spec: EnergySpec,
    config: AppConfig,
    *,
    run_id: str | None = None,
    should_abort: Callable[[], bool] | None = None,
    logger: logging.Logger | None = None,
) -> SolveResult:
    """Solve one energy spec end to end and certify the result."""
    logger = logger or get_logger()
    run_id = run_id or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    log_event(
        logger, logging.INFO, run_id, "run_started",
        width=spec.width, height=spec.height,
        pairwise_terms=len(spec.pairwise),
        seeds_fg=len(spec.seeds.foreground),
        seeds_bg=len(spec.seeds.background),
    )

    graph = build_graph(
        spec,
        chunk_rows=config.chunk_rows,
        max_pixels=config.max_pixels,
        max_edges=config.max_edges,
        run_id=run_id,
        logger=logger,
        should_abort=should_abort,
    )
    if should_abort is not None and should_abort():
        raise ComputationError("job_cancelled", "aborted before solve")
    solve = solve_maxflow(graph)
    log_event(
        logger, logging.INFO, run_id, "maxflow_done",
        flow=round(solve.flow_value, 6), phases=solve.phases,
        source_side=solve.source_side_size,
    )
    certificate = build_certificate(
        spec, graph, solve,
        tolerance=config.flow_tolerance,
        run_scipy_check=(
            config.scipy_cross_check
            and graph.num_nodes <= config.cross_check_max_nodes
        ),
        run_id=run_id,
        logger=logger,
    )
    stats = {
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "graph_nodes": float(graph.num_nodes),
        "graph_edges": float(graph.num_edges),
        "maxflow_phases": float(solve.phases),
    }
    log_event(
        logger, logging.INFO, run_id, "run_finished",
        energy=round(certificate.energy.total, 6),
        data=round(certificate.energy.data, 6),
        smoothness=round(certificate.energy.smoothness, 6),
        elapsed=stats["elapsed_seconds"],
    )
    return make_result(spec, solve, certificate, run_id=run_id, stats=stats)
