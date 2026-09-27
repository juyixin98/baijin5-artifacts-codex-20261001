"""Graph release / retain policy."""

import numpy as np
import pytest

from minigrad import GraphFreedError, Tensor


def test_second_backward_without_retain_rejected():
    x = Tensor(2.0, requires_grad=True)
    y = x * x
    y.backward()
    with pytest.raises(GraphFreedError) as excinfo:
        y.backward()
    assert excinfo.value.op_name == "mul"


def test_retain_graph_allows_repeated_backward():
    x = Tensor(2.0, requires_grad=True)
    y = x * x
    y.backward(retain_graph=True)
    y.backward()  # final call may release
    np.testing.assert_allclose(x.grad, 8.0)  # 2 * (2*2)


def test_freed_graph_releases_saved_references():
    x = Tensor([1.0, 2.0], requires_grad=True)
    y = x * x
    node = y._node
    assert node.saved  # mul saved its inputs
    y.sum().backward()
    assert node.freed is True
    assert node.saved == ()
    assert node.backward_fn is None


def test_partial_graph_release_only_affects_traversed_nodes():
    x = Tensor(2.0, requires_grad=True)
    a = x * x      # node 1
    b = x + 1.0    # node 2 (independent branch)
    a.backward()
    with pytest.raises(GraphFreedError):
        a.backward()
    b.backward()   # untouched branch still works
    np.testing.assert_allclose(x.grad, 4.0 + 1.0)
