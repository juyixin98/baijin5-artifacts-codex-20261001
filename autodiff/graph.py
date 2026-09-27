"""Computational graph engine: topological backward + graph lifetime policy.

Backward semantics
------------------
* Reverse topological traversal from the loss tensor's node.
* Every node's VJP feeds a per-node cotangent table; contributions from
  multiple consumers are **accumulated** (a node used by several branches
  receives one term per branch - acceptance rule 1).
* Only *leaf* tensors (user-created ``requires_grad=True`` tensors with no
  producing node) receive ``.grad``; intermediate tensors stay clean.
* ``grad is None`` means a leaf was never part of / never reached by the
  graph; an all-zeros array means it was reached with a zero cotangent
  (acceptance rule 3).

Version detection (acceptance rule 2)
-------------------------------------
Each node snapshots its inputs' versions on construction.  Before backward
traverses a node, current versions are compared.  A sanctioned in-place write
(``Tensor.set_data``) after graph construction bumps the version, so the
backward is **rejected** with :class:`StaleGraphError` and no ``.grad`` is
populated.  Raw ``tensor.data[...] = ...`` is blocked earlier by read-only
storage.

Graph release / retain (acceptance rule 4)
------------------------------------------
* ``backward(retain_graph=False)`` (default): after the pass, every visited
  node drops its input/output references, breaking the Python reference cycle
  and freeing intermediates.  A second backward on the same graph is rejected
  with :class:`GraphReleasedError`.
* ``backward(retain_graph=True)``: nodes are kept and gradients accumulate
  across repeated backwards (each call first seeds the loss cotangent, leaf
  grads are *not* cleared unless the caller does so).
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from .config import get_config
from .ops import Node
from .tensor import Tensor, _DEFAULT_DTYPE


class AutodiffError(RuntimeError):
    """Base class for graph-engine failures."""


class StaleGraphError(AutodiffError):
    """Raised (categorised REJECTED) when an input tensor changed versions."""


class GraphReleasedError(AutodiffError):
    """Raised when backward is re-run on a released graph."""


class NonScalarLossError(AutodiffError):
    """Raised when backward is called on a non-scalar without an upstream grad."""


def _build_topo(root: Tensor) -> list[Node]:
    """Return nodes in forward (topological) order via iterative DFS."""
    order: list[Node] = []
    visited: set[int] = set()
    # Stack of (node, expanded) pairs to emulate post-order DFS without
    # recursion (graphs may be deep).
    stack: list[tuple[Node, bool]] = []
    root_node = root._node
    if root_node is None:
        return order
    stack.append((root_node, False))
    while stack:
        node, expanded = stack.pop()
        if id(node) in visited:
            continue
        if expanded:
            visited.add(id(node))
            order.append(node)
            continue
        stack.append((node, True))
        for inp in node.inputs:
            if inp._node is not None and id(inp._node) not in visited:
                stack.append((inp._node, False))
    return order


def _check_versions(order: list[Node]) -> None:
    """Reject the graph if any captured input was mutated after construction."""
    for node in order:
        stale = node.is_stale()
        if stale:
            details = ", ".join(
                f"input#{idx} version {captured}->{current}"
                for idx, captured, current in stale
            )
            raise StaleGraphError(
                f"refusing backward on stale graph: {type(node).__name__} "
                f"captured tensors that were modified in place ({details})"
            )


def _release(order: list[Node]) -> None:
    """Release strategy: detach all nodes and clear tensor->node back-links."""
    for node in order:
        out = node.output
        node.release()
        if out is not None:
            out._node = None
            # Mark non-leaf outputs as graph-detached so a later backward is
            # rejected instead of treating the orphaned result as a fresh leaf.
            out._graph_detached = True


def backward(
    loss: Tensor,
    grad_output: Optional[np.ndarray] = None,
    *,
    retain_graph: bool = False,
) -> None:
    """Run reverse-mode autodiff, accumulating gradients on graph leaves.

    Parameters
    ----------
    loss:
        Tensor to differentiate.  Must be scalar unless *grad_output* given.
    grad_output:
        Upstream cotangent, same shape as *loss*.  Defaults to ones for a
        scalar loss.
    retain_graph:
        Keep the graph for another backward pass; default releases it.
    """
    cfg = get_config()
    if loss.is_graph_detached():
        raise GraphReleasedError(
            "this tensor's graph was released by a previous backward() "
            "(retain_graph=False); rebuild the forward pass or pass "
            "retain_graph=True"
        )
    if loss._node is None:
        if not loss.requires_grad:
            # A detached/constant scalar: nothing to differentiate.  This is
            # accepted as a no-op rather than an error.
            return
        # Genuine leaf passed directly to backward: seed its own gradient.
        seed = _validate_seed(loss, grad_output)
        loss.accumulate_grad(seed)
        return

    order = _build_topo(loss)
    if len(order) > cfg.max_backward_nodes:
        raise AutodiffError(
            f"backward traversed {len(order)} nodes, exceeding limit "
            f"{cfg.max_backward_nodes} (possible cycle)"
        )

    # Version gate: check the whole graph before touching any .grad so a
    # rejection leaves leaf gradients untouched.
    _check_versions(order)

    seed = _validate_seed(loss, grad_output)

    # Per-node cotangent accumulator keyed by id(node).
    node_grads: dict[int, np.ndarray] = {id(loss._node): seed}

    for node in reversed(order):
        g = node_grads.pop(id(node), None)
        if g is None:
            # Node unreached by cotangent (disconnected branch): its inputs get
            # nothing from this node - distinct from an explicit zero.
            continue
        input_grads = node.backward(g)
        for inp, ig in zip(node.inputs, input_grads):
            if ig is None or not inp.requires_grad:
                continue
            if inp._node is None:
                # Leaf: accumulate into the user-visible gradient.
                inp.accumulate_grad(np.asarray(ig, dtype=_DEFAULT_DTYPE))
            else:
                # Intermediate: accumulate into the per-node table so a node
                # shared by multiple branches sums every contribution.
                key = id(inp._node)
                ig = np.asarray(ig, dtype=_DEFAULT_DTYPE)
                if key in node_grads:
                    node_grads[key] = node_grads[key] + ig
                else:
                    node_grads[key] = ig

    # Leaves reached with an explicit zero cotangent must present a zero
    # array, not None.  Walk leaves and mark the reached-but-unaccumulated
    # distinction via a separate pass: a leaf that is part of the graph yet
    # received no contribution only happens when its path cotangent was never
    # sent; in a connected graph every reachable leaf gets at least one call.
    if not retain_graph:
        _release(order)
        # The loss itself is an intermediate unless it is also a leaf; its
        # node link was cleared by release.
    return None


def _validate_seed(loss: Tensor, grad_output: Optional[np.ndarray]) -> np.ndarray:
    if grad_output is None:
        if loss.shape != ():
            raise NonScalarLossError(
                f"backward requires grad_output for non-scalar output of shape "
                f"{loss.shape}"
            )
        return np.ones((), dtype=_DEFAULT_DTYPE)
    seed = np.asarray(grad_output, dtype=_DEFAULT_DTYPE)
    if seed.shape != loss.shape:
        raise AutodiffError(
            f"grad_output shape {seed.shape} does not match loss shape {loss.shape}"
        )
    return seed
