"""Orchestration: spec -> graph -> solve -> certificate -> result.

This is the only module that wires the contracts, the graph builder, the
kernel and the certificate together.  It owns no numerical logic itself.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .certificate import CutCertificate, verify_cut
from .config import Settings
from .contracts import SegmentationSpec
from .graph import BuildCancelled, STGraph, build_st_graph
from .maxflow import solve_min_cut
from .runlog import RunLogger, new_run_id


@dataclass(frozen=True)
class SegmentationResult:
    run_id: str
    certificate: CutCertificate
    graph_constant: float
    big_m: float
    num_edges: int
    elapsed_ms: float

    @property
    def labels(self):
        return self.certificate.labels

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "labels": self.certificate.labels.astype(int).tolist(),
            "energy": {
                **self.certificate.energy.to_dict(),
                "graph_constant": self.graph_constant,
            },
            "certificate": self.certificate.to_dict(),
            "meta": {
                "num_edges": self.num_edges,
                "big_m": self.big_m,
                "solver": "dinic-float64",
                "elapsed_ms": self.elapsed_ms,
            },
        }


def run_segmentation(
    spec: SegmentationSpec,
    *,
    settings: Settings | None = None,
    run_id: str | None = None,
    chunk_rows: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> SegmentationResult:
    """Solve a validated spec and return a certified result.

    Raises BuildCancelled if ``should_cancel`` fires during graph build.
    """
    settings = settings or Settings()
    run_id = run_id or new_run_id()
    log = RunLogger(run_id)
    started = time.perf_counter()

    log.state("spec.accepted", height=spec.height, width=spec.width,
              seeds=len(spec.seeds), pairwise=spec.pairwise)

    graph: STGraph = build_st_graph(
        spec,
        settings=settings,
        chunk_rows=chunk_rows,
        on_chunk=on_progress,
        should_cancel=should_cancel,
    )
    log.state("graph.built", nodes=graph.num_nodes, edges=graph.num_edges,
              constant=graph.constant, big_m=graph.big_m,
              seed_arcs=graph.num_seed_arcs)

    flow = solve_min_cut(graph)
    log.state("kernel.solved", flow_value=flow.flow_value,
              source_side=int(flow.source_set[: graph.num_pixels].sum()))

    certificate = verify_cut(
        spec, graph, flow, tolerance=settings.energy_tolerance,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    log.decision(
        "certificate.verified",
        reason="flow == cut == energy within tolerance and seeds satisfied",
        flow_value=certificate.flow_value,
        cut_capacity=certificate.cut_capacity,
        energy_total=certificate.energy.total,
        gap_flow_cut=certificate.abs_gap_flow_cut,
        gap_cut_energy=certificate.abs_gap_cut_energy,
        tolerance=certificate.tolerance,
    )
    return SegmentationResult(
        run_id=run_id,
        certificate=certificate,
        graph_constant=graph.constant,
        big_m=graph.big_m,
        num_edges=graph.num_edges,
        elapsed_ms=elapsed_ms,
    )


__all__ = ["SegmentationResult", "run_segmentation", "BuildCancelled"]
