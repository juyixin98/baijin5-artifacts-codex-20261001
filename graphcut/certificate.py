"""Cut certificate: independent verification of a solver result.

The graph represents the energy up to an additive constant that is folded
into t-links during construction, so the verified identities are

    flow_value == cut_capacity                      (strong duality)
    cut_capacity + graph_constant == energy_total   (graph correspondence)

where ``cut_capacity`` counts arcs only.  Concretely the certificate

1. derives labels from the residual reachable set (source side -> label 1);
2. recomputes the cut capacity *directly from the arc list*;
3. recomputes the energy *directly from the spec* (graphcut.energy);
4. checks the two identities above within tolerance — together they are the
   strong-duality witness that the labeling is a global optimum;
5. checks every hard seed is satisfied.

Any mismatch raises ComputationError — the service never returns an
unverified labeling.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .contracts import SegmentationSpec
from .energy import EnergyBreakdown, evaluate_energy
from .errors import ComputationError
from .graph import STGraph
from .maxflow import MaxFlowResult


@dataclass(frozen=True)
class CutCertificate:
    labels: np.ndarray
    energy: EnergyBreakdown
    flow_value: float
    cut_capacity: float       # arcs only
    graph_constant: float     # additive constant folded into the graph
    abs_gap_flow_cut: float
    abs_gap_cut_energy: float
    tolerance: float
    source_side_pixels: int
    cut_arcs: int
    seeds_satisfied: bool
    terminals_consistent: bool
    verified: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "flow_value": self.flow_value,
            "cut_capacity": self.cut_capacity,
            "graph_constant": self.graph_constant,
            "energy_total": self.energy.total,
            "abs_gap_flow_cut": self.abs_gap_flow_cut,
            "abs_gap_cut_energy": self.abs_gap_cut_energy,
            "tolerance": self.tolerance,
            "source_side_pixels": self.source_side_pixels,
            "cut_arcs": self.cut_arcs,
            "seeds_satisfied": self.seeds_satisfied,
            "terminals_consistent": self.terminals_consistent,
            "verified": self.verified,
        }


def cut_capacity_of(graph: STGraph, in_source_side: np.ndarray) -> tuple[float, int]:
    """Capacity of arcs crossing S->T, computed straight from the arc list."""
    cross = in_source_side[graph.edges_from] & ~in_source_side[graph.edges_to]
    return float(graph.edges_cap[cross].sum()), int(cross.sum())


def verify_cut(
    spec: SegmentationSpec,
    graph: STGraph,
    flow: MaxFlowResult,
    *,
    tolerance: float,
) -> CutCertificate:
    pixel_side = flow.source_set[: graph.num_pixels]
    labels = pixel_side.astype(np.int8).reshape(spec.height, spec.width)

    cut_cap, cut_arcs = cut_capacity_of(graph, flow.source_set)
    energy = evaluate_energy(spec, labels)

    adjusted_cut = cut_cap + graph.constant
    tol = tolerance * max(1.0, abs(energy.total), abs(flow.flow_value),
                          abs(adjusted_cut))
    gap_flow_cut = abs(flow.flow_value - cut_cap)
    gap_cut_energy = abs(adjusted_cut - energy.total)

    seeds_satisfied = all(
        int(labels[s.row, s.col]) == s.label for s in spec.seeds
    )
    terminals_consistent = bool(
        flow.source_set[graph.source] and not flow.source_set[graph.sink]
    )

    problems: list[str] = []
    if gap_flow_cut > tol:
        problems.append(f"flow {flow.flow_value!r} != cut {cut_cap!r}")
    if gap_cut_energy > tol:
        problems.append(
            f"cut {cut_cap!r} + constant {graph.constant!r} != "
            f"energy {energy.total!r}")
    if not seeds_satisfied:
        problems.append("hard seeds not satisfied by the labeling")
    if not terminals_consistent:
        problems.append("source/sink terminal sides are inconsistent")
    if problems:
        raise ComputationError(
            "cut certificate verification failed: " + "; ".join(problems),
            code="CERTIFICATE_MISMATCH",
            details={
                "problems": problems,
                "flow_value": flow.flow_value,
                "cut_capacity": cut_cap,
                "graph_constant": graph.constant,
                "energy_total": energy.total,
                "tolerance": tol,
            },
        )

    return CutCertificate(
        labels=labels,
        energy=energy,
        flow_value=flow.flow_value,
        cut_capacity=cut_cap,
        graph_constant=graph.constant,
        abs_gap_flow_cut=gap_flow_cut,
        abs_gap_cut_energy=gap_cut_energy,
        tolerance=tol,
        source_side_pixels=int(pixel_side.sum()),
        cut_arcs=cut_arcs,
        seeds_satisfied=seeds_satisfied,
        terminals_consistent=terminals_consistent,
        verified=True,
    )
