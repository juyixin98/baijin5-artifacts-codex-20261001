"""Computation graph: nodes, version checking, and the backward engine.

Graph lifecycle policy
----------------------
* Each differentiable op creates one ``Node`` capturing its input tensors,
  the tensors it *saves* for backward (only those whose values the backward
  function actually reads), and the version counters of the saved tensors.
* ``run_backward`` walks the graph in reverse topological order, accumulating
  gradients of shared nodes (a tensor used by several consumers receives the
  sum of all incoming gradients).
* Release policy: after a backward with ``retain_graph=False`` every node in
  the traversed subgraph is freed (saved tensors and backward closure
  dropped). A second backward through a freed node raises ``GraphFreedError``.
  With ``retain_graph=True`` nothing is freed and gradients keep accumulating.
"""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np

from .errors import GraphFreedError, InplaceModificationError


class Node:
    """One recorded operation in the computation graph."""

    __slots__ = (
        "op_name",
        "inputs",
        "out",
        "backward_fn",
        "saved",
        "saved_versions",
        "freed",
    )

    def __init__(
        self,
        op_name: str,
        inputs: tuple,
        backward_fn: Callable[[np.ndarray], tuple],
        saved: tuple = (),
    ):
        self.op_name = op_name
        self.inputs = tuple(inputs)
        self.out = None  # set by the ops layer once the output Tensor exists
        self.backward_fn = backward_fn
        self.saved = tuple(saved)
        self.saved_versions = tuple(t._version for t in self.saved)
        self.freed = False

    def check_versions(self) -> None:
        """Reject backward if any saved tensor was modified in place."""
        for tensor, version in zip(self.saved, self.saved_versions):
            if tensor._version != version:
                raise InplaceModificationError(
                    self.op_name, tensor, version, tensor._version
                )

    def free(self) -> None:
        """Release saved state; further backward through this node fails."""
        self.saved = ()
        self.saved_versions = ()
        self.backward_fn = None
        self.freed = True


def _topo_sort(root) -> list:
    """Depth-first topological order of nodes reachable from ``root``."""
    topo: list = []
    visited: set = set()

    def visit(tensor) -> None:
        node = tensor._node
        if node is None or id(node) in visited:
            return
        visited.add(id(node))
        for parent in node.inputs:
            visit(parent)
        topo.append(node)

    visit(root)
    return topo


def run_backward(root, grad: np.ndarray, retain_graph: bool = False) -> None:
    """Backpropagate ``grad`` from ``root`` through the recorded graph.

    Gradients accumulate into ``.grad`` of every reachable leaf tensor with
    ``requires_grad=True`` (a leaf is a tensor that is not the output of a
    recorded op). Leaves unreachable from ``root`` keep ``grad is None`` —
    "no gradient" is never conflated with an all-zeros gradient.
    """
    topo = _topo_sort(root)

    grads: dict[int, np.ndarray] = {id(root): grad}
    tensors: dict[int, object] = {id(root): root}
    for node in topo:
        tensors[id(node.out)] = node.out
        for parent in node.inputs:
            tensors[id(parent)] = parent

    for node in reversed(topo):
        upstream = grads.get(id(node.out))
        if upstream is None:
            continue
        if node.freed:
            raise GraphFreedError(node.op_name)
        node.check_versions()
        input_grads = node.backward_fn(upstream)
        for parent, parent_grad in zip(node.inputs, input_grads):
            if parent_grad is None or not parent.requires_grad:
                continue
            key = id(parent)
            if key in grads:
                grads[key] = grads[key] + parent_grad
            else:
                grads[key] = parent_grad

    if not retain_graph:
        for node in topo:
            node.free()

    for key, accumulated in grads.items():
        tensor = tensors.get(key)
        if tensor is None or not tensor.requires_grad:
            continue
        if tensor._node is not None:
            continue  # only leaves receive .grad (documented boundary)
        tensor.grad = accumulated if tensor.grad is None else tensor.grad + accumulated
