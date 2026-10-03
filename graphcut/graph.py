"""Lower an :class:`EnergySpec` to an s-t flow network.

Reduction (Kolmogorov & Zabih style). Convention: label 0 = source side,
label 1 = sink side; a directed edge u->v is cut when u is on the source
side and v on the sink side.

Unary terms ``(d0, d1)`` per pixel become terminal links plus a constant::

    d1 >= d0:  constant += d0,  edge s->p capacity d1 - d0
    d0 >  d1:  constant += d1,  edge p->t capacity d0 - d1

A pairwise term (v00, v01, v10, v11) on (p, q) becomes::

    constant += v00
    d1[p]    += v10 - v00
    d1[q]    += v11 - v10
    edge p->q capacity w = v01 + v10 - v00 - v11   (>= 0 iff submodular)

so that ``energy(x) = constant + cut_capacity(x)`` exactly. Submodularity
is validated upstream in :mod:`graphcut.energy`; this module re-checks the
edge weight is non-negative and refuses to build otherwise (no abs()).

Construction streams pairwise terms in row-band chunks so memory stays
bounded and chunked jobs can report progress / honour cancellation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .errors import ComputationError, ResourceExhaustedError
from .logging_utils import log_event
from .models import EnergySpec
from .seeds import apply_seeds


@dataclass(frozen=True)
class GraphData:
    """Directed capacitated graph in edge-list form.

    Nodes ``0 .. num_pixels-1`` are pixels (row-major), node ``num_pixels``
    is the source, ``num_pixels + 1`` the sink.
    """

    num_nodes: int
    source: int
    sink: int
    src: np.ndarray  # int64 edge tails
    dst: np.ndarray  # int64 edge heads
    cap: np.ndarray  # float64 capacities, all >= 0
    constant: float
    seed_weight: float

    @property
    def num_edges(self) -> int:
        return int(self.src.shape[0])


def _check_limits(spec: EnergySpec, max_pixels: int, max_edges: int) -> None:
    n = spec.num_pixels
    if n > max_pixels:
        raise ResourceExhaustedError(
            "too_many_pixels",
            f"image has {n} pixels, limit is {max_pixels}",
            details={"pixels": n, "limit": max_pixels},
        )
    # upper bound: 2 t-links per pixel + 1 edge per pairwise term
    edge_bound = 2 * n + len(spec.pairwise)
    if edge_bound > max_edges:
        raise ResourceExhaustedError(
            "too_many_edges",
            f"graph would need up to {edge_bound} edges, limit is {max_edges}",
            details={"edges": edge_bound, "limit": max_edges},
        )


def build_graph(
    spec: EnergySpec,
    *,
    chunk_rows: int,
    max_pixels: int,
    max_edges: int,
    run_id: str,
    logger: logging.Logger,
    should_abort: Callable[[], bool] | None = None,
) -> GraphData:
    """Build the flow network, streaming pairwise terms in row chunks.

    ``should_abort`` (optional) is polled between chunks; returning True
    raises :class:`ComputationError` with code ``job_cancelled`` so chunked
    jobs can be cancelled at chunk boundaries.
    """
    _check_limits(spec, max_pixels, max_edges)
    u0, u1, big_m = apply_seeds(spec)

    n = spec.num_pixels
    source, sink = n, n + 1
    d1 = u1.reshape(-1).astype(np.float64, copy=True)
    d0 = u0.reshape(-1).astype(np.float64, copy=True)
    constant = 0.0

    edge_src: list[int] = []
    edge_dst: list[int] = []
    edge_cap: list[float] = []

    terms = sorted(spec.pairwise, key=lambda t: min(t.p, t.q) // spec.width)
    chunk_size = max(1, chunk_rows) * spec.width
    for chunk_start in range(0, len(terms), max(1, chunk_size)):
        if should_abort is not None and should_abort():
            raise ComputationError(
                "job_cancelled", "graph construction aborted at chunk boundary"
            )
        chunk = terms[chunk_start:chunk_start + max(1, chunk_size)]
        for term in chunk:
            w = term.v01 + term.v10 - term.v00 - term.v11
            if w < 0.0:
                # validate_spec already rejects this; a negative weight here
                # means the caller bypassed validation — a bug, not bad input.
                raise ComputationError(
                    "negative_edge_weight",
                    f"pairwise ({term.p}, {term.q}) yields negative edge "
                    f"weight {w}; refusing to take abs()",
                )
            constant += term.v00
            d1[term.p] += term.v10 - term.v00
            d1[term.q] += term.v11 - term.v10
            if w > 0.0:
                edge_src.append(term.p)
                edge_dst.append(term.q)
                edge_cap.append(w)
        log_event(
            logger, logging.DEBUG, run_id, "graph_chunk_built",
            chunk_start=chunk_start, terms=len(chunk),
            edges_so_far=len(edge_src),
        )

    for p in range(n):
        if d1[p] >= d0[p]:
            constant += d0[p]
            if d1[p] > d0[p]:
                edge_src.append(source)
                edge_dst.append(p)
                edge_cap.append(d1[p] - d0[p])
        else:
            constant += d1[p]
            edge_src.append(p)
            edge_dst.append(sink)
            edge_cap.append(d0[p] - d1[p])

    graph = GraphData(
        num_nodes=n + 2,
        source=source,
        sink=sink,
        src=np.asarray(edge_src, dtype=np.int64),
        dst=np.asarray(edge_dst, dtype=np.int64),
        cap=np.asarray(edge_cap, dtype=np.float64),
        constant=constant,
        seed_weight=big_m,
    )
    log_event(
        logger, logging.INFO, run_id, "graph_built",
        nodes=graph.num_nodes, edges=graph.num_edges,
        constant=round(constant, 6), seed_weight=big_m,
    )
    return graph
