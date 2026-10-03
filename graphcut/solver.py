"""Numerical kernel: Dinic max-flow on the s-t network, plus cut extraction.

Implemented in-house (not delegated to SciPy) so the residual graph is
directly available for the cut certificate; :mod:`graphcut.verify` then
uses SciPy's independent implementation as a cross-check.

Capacities are float64; comparisons use an absolute epsilon. The kernel
raises :class:`ComputationError` on internal inconsistency — input problems
are caught upstream before this module runs.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from .errors import ComputationError
from .graph import GraphData

EPS = 1e-12


@dataclass
class _ResidualNetwork:
    """Adjacency-list residual network. Edge 2i is the reverse of edge 2i+1."""

    num_nodes: int
    to: list[int]
    cap: list[float]  # residual capacities, mutated in place
    next_: list[int]
    head: list[int]

    @classmethod
    def from_graph(cls, graph: GraphData) -> "_ResidualNetwork":
        head = [-1] * graph.num_nodes
        to: list[int] = []
        cap: list[float] = []
        next_: list[int] = []

        def add(u: int, v: int, c: float) -> None:
            to.append(v)
            cap.append(c)
            next_.append(head[u])
            head[u] = len(to) - 1

        for u, v, c in zip(graph.src, graph.dst, graph.cap):
            add(int(u), int(v), float(c))
            add(int(v), int(u), 0.0)
        return cls(graph.num_nodes, to, cap, next_, head)


def _bfs_levels(net: _ResidualNetwork, source: int, sink: int) -> list[int]:
    level = [-1] * net.num_nodes
    level[source] = 0
    queue = deque([source])
    while queue:
        u = queue.popleft()
        e = net.head[u]
        while e != -1:
            v = net.to[e]
            if net.cap[e] > EPS and level[v] < 0:
                level[v] = level[u] + 1
                queue.append(v)
            e = net.next_[e]
    return level


def _send_flow(
    net: _ResidualNetwork,
    level: list[int],
    iter_edge: list[int],
    source: int,
    sink: int,
) -> float:
    """One blocking-flow pass, iterative DFS to avoid recursion limits."""
    total = 0.0
    stack: list[tuple[int, int]] = []  # (node, edge used to reach next node)
    u = source
    while True:
        if u == sink:
            # bottleneck on the path stored in `stack`
            bottleneck = min(net.cap[e] for _, e in stack)
            for node, e in stack:
                net.cap[e] -= bottleneck
                net.cap[e ^ 1] += bottleneck
            total += bottleneck
            # retreat to the shallowest saturated edge: everything beyond
            # it is unreachable through this path
            saturated_at = next(
                i for i, (_, e) in enumerate(stack) if net.cap[e] <= EPS
            )
            u = stack[saturated_at][0]
            del stack[saturated_at:]
            continue
        advanced = False
        e = iter_edge[u]
        while e != -1:
            v = net.to[e]
            if net.cap[e] > EPS and level[v] == level[u] + 1:
                stack.append((u, e))
                iter_edge[u] = e
                u = v
                advanced = True
                break
            e = net.next_[e]
            iter_edge[u] = e
        if not advanced:
            level[u] = -1  # prune dead end
            if not stack:
                return total
            u, _ = stack.pop()


@dataclass(frozen=True)
class SolveOutput:
    flow_value: float
    labeling: np.ndarray  # (num_pixels,) of 0/1; 0 = source side
    phases: int
    source_side_size: int


def solve_maxflow(graph: GraphData) -> SolveOutput:
    """Run Dinic to optimality and extract the min-cut labeling."""
    net = _ResidualNetwork.from_graph(graph)
    flow = 0.0
    phases = 0
    while True:
        level = _bfs_levels(net, graph.source, graph.sink)
        if level[graph.sink] < 0:
            break
        iter_edge = list(net.head)
        flow += _send_flow(net, level, iter_edge, graph.source, graph.sink)
        phases += 1
        if phases > 4 * graph.num_nodes:
            raise ComputationError(
                "maxflow_not_converged",
                f"Dinic exceeded {phases} phases on {graph.num_nodes} nodes",
            )

    reachable = _reachable_from(net, graph.source)
    num_pixels = graph.num_nodes - 2
    labeling = np.array(
        [0 if reachable[p] else 1 for p in range(num_pixels)], dtype=np.int8
    )
    if not (flow == flow and flow >= 0.0):  # NaN / negative guard
        raise ComputationError(
            "maxflow_invalid_value", f"solver returned flow={flow!r}"
        )
    return SolveOutput(
        flow_value=flow,
        labeling=labeling,
        phases=phases,
        source_side_size=int(reachable.sum()) - 1,  # exclude source itself
    )


def _reachable_from(net: _ResidualNetwork, source: int) -> np.ndarray:
    seen = np.zeros(net.num_nodes, dtype=bool)
    seen[source] = True
    queue = deque([source])
    while queue:
        u = queue.popleft()
        e = net.head[u]
        while e != -1:
            v = net.to[e]
            if net.cap[e] > EPS and not seen[v]:
                seen[v] = True
                queue.append(v)
            e = net.next_[e]
    return seen
