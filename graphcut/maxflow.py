"""Numerical kernel: s-t max flow / min cut via Dinic's algorithm.

The kernel is deliberately self-contained: SciPy's ``maximum_flow`` only
accepts integer capacities, while this service needs exact float64 energy
accounting, so the kernel implements Dinic's algorithm directly over
float64 arc capacities (BFS level graph + iterative blocking-flow DFS, so
path length is not bounded by the Python recursion limit).

The kernel knows nothing about images or energies — only directed
capacitated graphs.  Everything it returns is independently verified by
:mod:`graphcut.certificate` before being trusted.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .errors import ComputationError
from .graph import STGraph

# Residual arcs at or below this threshold are treated as saturated.
_EPS_REL = 1e-12


@dataclass(frozen=True)
class MaxFlowResult:
    flow_value: float
    source_set: np.ndarray  # bool array over nodes; True = reachable from source in residual


class _ResidualNetwork:
    """Arc-array residual network; arc 2k and 2k+1 are a residual pair."""

    def __init__(self, num_nodes: int, edges_from: np.ndarray,
                 edges_to: np.ndarray, edges_cap: np.ndarray) -> None:
        m = int(edges_from.shape[0])
        self.num_nodes = num_nodes
        self.frm = np.empty(2 * m, dtype=np.int64)
        self.to = np.empty(2 * m, dtype=np.int64)
        self.cap = np.empty(2 * m, dtype=np.float64)
        self.frm[0::2] = edges_from
        self.frm[1::2] = edges_to
        self.to[0::2] = edges_to
        self.to[1::2] = edges_from
        self.cap[0::2] = edges_cap
        self.cap[1::2] = 0.0
        # CSR adjacency over source nodes.
        order = np.argsort(self.frm, kind="stable")
        self.adj = order.astype(np.int64)
        counts = np.bincount(self.frm, minlength=num_nodes)
        self.offset = np.zeros(num_nodes + 1, dtype=np.int64)
        np.cumsum(counts, out=self.offset[1:])
        self.eps = _EPS_REL * max(1.0, float(edges_cap.max()) if m else 1.0)

    def bfs_levels(self, source: int, sink: int) -> np.ndarray | None:
        """Level graph from source; None if sink is unreachable."""
        level = np.full(self.num_nodes, -1, dtype=np.int64)
        level[source] = 0
        frontier = [source]
        while frontier:
            next_frontier: list[int] = []
            for v in frontier:
                lv = level[v] + 1
                for k in range(self.offset[v], self.offset[v + 1]):
                    e = self.adj[k]
                    w = int(self.to[e])
                    if self.cap[e] > self.eps and level[w] < 0:
                        level[w] = lv
                        next_frontier.append(w)
            frontier = next_frontier
        return level if level[sink] >= 0 else None

    def blocking_flow(self, source: int, sink: int, level: np.ndarray) -> float:
        """Iterative Dinic DFS with current-arc optimization."""
        ptr = self.offset[:-1].copy()  # per-node cursor into adj
        total = 0.0
        cap, to, adj, eps = self.cap, self.to, self.adj, self.eps
        while True:
            path: list[int] = []
            v = source
            while v != sink:
                advanced = False
                end = self.offset[v + 1]
                while ptr[v] < end:
                    e = int(adj[ptr[v]])
                    w = int(to[e])
                    if cap[e] > eps and level[w] == level[v] + 1:
                        path.append(e)
                        v = w
                        advanced = True
                        break
                    ptr[v] += 1
                if not advanced:
                    if not path:
                        return total
                    ptr[v] = self.offset[v + 1]  # node v is dead-end
                    e = path.pop()
                    v = int(self.frm[e])
                    ptr[v] += 1
            bottleneck = min(cap[e] for e in path)
            for e in path:
                cap[e] -= bottleneck
                cap[e ^ 1] += bottleneck
            total += bottleneck

    def reachable_from(self, source: int) -> np.ndarray:
        seen = np.zeros(self.num_nodes, dtype=bool)
        seen[source] = True
        stack = [source]
        while stack:
            v = stack.pop()
            for k in range(self.offset[v], self.offset[v + 1]):
                e = int(self.adj[k])
                w = int(self.to[e])
                if self.cap[e] > self.eps and not seen[w]:
                    seen[w] = True
                    stack.append(w)
        return seen


def solve_min_cut(graph: STGraph) -> MaxFlowResult:
    """Compute the min s-t cut. Raises ComputationError on kernel failure."""
    if graph.num_edges and (not np.all(np.isfinite(graph.edges_cap))
                            or float(graph.edges_cap.min()) < 0.0):
        raise ComputationError(
            "graph contains negative or non-finite arc capacities",
            code="KERNEL_INPUT_INVALID",
        )
    try:
        net = _ResidualNetwork(graph.num_nodes, graph.edges_from,
                               graph.edges_to, graph.edges_cap)
        source_cap = float(graph.edges_cap[graph.edges_from == graph.source].sum())
        while True:
            level = net.bfs_levels(graph.source, graph.sink)
            if level is None:
                break
            net.blocking_flow(graph.source, graph.sink, level)
        residual_out = float(
            net.cap[0::2][graph.edges_from == graph.source].sum()
        ) if graph.num_edges else 0.0
        flow_value = source_cap - residual_out
        source_set = net.reachable_from(graph.source)
    except ComputationError:
        raise
    except Exception as exc:  # kernel failure must surface as our taxonomy
        raise ComputationError(
            f"max-flow kernel failed: {exc}",
            code="MAXFLOW_KERNEL_FAILURE",
            details={"exception": type(exc).__name__, "message": str(exc)},
        ) from exc

    if not np.isfinite(flow_value) or flow_value < -net.eps:
        raise ComputationError(
            f"max-flow kernel produced invalid flow value {flow_value!r}",
            code="MAXFLOW_INVALID_RESULT",
            details={"flow_value": flow_value},
        )
    flow_value = max(flow_value, 0.0)
    if source_set[graph.sink]:
        raise ComputationError(
            "sink is reachable from source in the residual graph; "
            "the returned flow is not maximal",
            code="MAXFLOW_NOT_MAXIMAL",
        )
    return MaxFlowResult(flow_value=flow_value, source_set=source_set)
