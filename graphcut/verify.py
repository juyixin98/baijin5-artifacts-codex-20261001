"""Cut certificate and independent verification.

After the kernel solves an instance, this module independently:

1. recomputes the cut capacity of the returned labeling straight from the
   graph edge list (no solver state),
2. recomputes the energy of the labeling straight from the spec via
   :func:`graphcut.energy.evaluate_energy`,
3. checks ``energy == graph_constant + flow_value == graph_constant +
   cut_capacity`` within tolerance,
4. checks every hard seed carries its forced label,
5. optionally re-solves with SciPy's integer max-flow on scaled capacities
   (an independent implementation) and compares flow values.

Any inconsistency is a :class:`ComputationError` — the inputs were already
validated, so a mismatch means the pipeline itself is broken.
"""

from __future__ import annotations

import logging

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import maximum_flow

from .energy import evaluate_energy
from .errors import ComputationError
from .graph import GraphData
from .logging_utils import log_event
from .models import CutCertificate, EnergySpec, SolveResult
from .solver import SolveOutput

SCIPY_CAPACITY_SCALE = 1_000_000  # float -> int64 for SciPy's solver


def cut_capacity(graph: GraphData, labeling: np.ndarray) -> float:
    """Capacity of edges crossing from the source side to the sink side."""
    side = np.zeros(graph.num_nodes, dtype=np.int8)
    side[: labeling.size] = labeling
    side[graph.source] = 0
    side[graph.sink] = 1
    crossing = (side[graph.src] == 0) & (side[graph.dst] == 1)
    return float(graph.cap[crossing].sum(dtype=np.float64))


def seeds_satisfied(spec: EnergySpec, labeling: np.ndarray) -> bool:
    flat = labeling.reshape(-1)
    return all(int(flat[i]) == 1 for i in spec.seeds.foreground) and all(
        int(flat[i]) == 0 for i in spec.seeds.background
    )


def scipy_cross_check(graph: GraphData) -> float:
    """Max-flow value from SciPy's independent implementation.

    Capacities are scaled to int64 (SciPy requires integer capacities), so
    the result is approximate up to the scaling; callers compare with a
    tolerance proportional to the scale.
    """
    scaled = np.rint(graph.cap * SCIPY_CAPACITY_SCALE).astype(np.int64)
    matrix = csr_matrix(
        (scaled, (graph.src, graph.dst)),
        shape=(graph.num_nodes, graph.num_nodes),
    )
    result = maximum_flow(matrix, graph.source, graph.sink)
    return result.flow_value / SCIPY_CAPACITY_SCALE


def build_certificate(
    spec: EnergySpec,
    graph: GraphData,
    solve: SolveOutput,
    *,
    tolerance: float,
    run_scipy_check: bool,
    run_id: str,
    logger: logging.Logger,
) -> CutCertificate:
    labeling = solve.labeling
    energy = evaluate_energy(spec, labeling.reshape(spec.height, spec.width))
    cut = cut_capacity(graph, labeling)
    seeds_ok = seeds_satisfied(spec, labeling)

    scipy_flow: float | None = None
    if run_scipy_check:
        scipy_flow = scipy_cross_check(graph)

    problems: list[str] = []
    if abs(solve.flow_value - cut) > tolerance:
        problems.append(
            f"flow {solve.flow_value} != cut capacity {cut} (tol {tolerance})"
        )
    graph_energy = graph.constant + solve.flow_value
    if abs(energy.total - graph_energy) > tolerance:
        problems.append(
            f"energy {energy.total} != constant+flow {graph_energy} "
            f"(tol {tolerance})"
        )
    if not seeds_ok:
        problems.append("hard seeds violated by the computed labeling")
    if scipy_flow is not None and abs(scipy_flow - solve.flow_value) > max(
        tolerance, 10.0 / SCIPY_CAPACITY_SCALE * max(1.0, graph.num_edges)
    ):
        problems.append(
            f"scipy cross-check flow {scipy_flow} disagrees with "
            f"{solve.flow_value}"
        )

    certificate = CutCertificate(
        flow_value=solve.flow_value,
        cut_capacity=cut,
        graph_constant=graph.constant,
        energy=energy,
        seeds_satisfied=seeds_ok,
        scipy_flow_value=scipy_flow,
        consistent=not problems,
        tolerance=tolerance,
    )
    if problems:
        log_event(
            logger, logging.ERROR, run_id, "certificate_failed",
            reasons=problems,
        )
        raise ComputationError(
            "certificate_mismatch",
            "cut certificate failed: " + "; ".join(problems),
            details={"problems": problems},
        )
    log_event(
        logger, logging.INFO, run_id, "certificate_ok",
        flow=round(solve.flow_value, 6), cut=round(cut, 6),
        energy=round(energy.total, 6), scipy_flow=scipy_flow,
    )
    return certificate


def make_result(
    spec: EnergySpec,
    solve: SolveOutput,
    certificate: CutCertificate,
    *,
    run_id: str,
    stats: dict[str, float],
) -> SolveResult:
    return SolveResult(
        run_id=run_id,
        width=spec.width,
        height=spec.height,
        labeling=solve.labeling.reshape(spec.height, spec.width),
        energy=certificate.energy,
        certificate=certificate,
        stats=stats,
    )
