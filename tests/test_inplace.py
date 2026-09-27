"""In-place modification detection via version counters.

Rejection rule: an op *saves* exactly the tensors its backward reads
(mul/div/pow/log save inputs; add/sub/sum do not). Mutating a saved tensor
in place before backward must raise InplaceModificationError; mutating a
tensor that no backward depends on is allowed.
"""

import numpy as np
import pytest

from minigrad import InplaceModificationError, Tensor
from minigrad.training import Parameter, SGD


def test_inplace_write_to_saved_tensor_rejected():
    x = Tensor([1.0, 2.0, 3.0], requires_grad=True, name="x")
    y = x * x  # mul saves x for backward
    x[0] = 5.0
    with pytest.raises(InplaceModificationError) as excinfo:
        y.sum().backward()
    assert excinfo.value.op_name == "mul"
    assert excinfo.value.tensor_name == "x"
    assert excinfo.value.expected_version == 0
    assert excinfo.value.actual_version == 1


def test_inplace_write_to_non_saved_tensor_allowed():
    x = Tensor([1.0, 2.0, 3.0], requires_grad=True)
    y = x + 1.0  # add saves nothing
    x[0] = 99.0
    y.sum().backward()  # must not raise
    np.testing.assert_allclose(x.grad, [1.0, 1.0, 1.0])


def test_inplace_write_after_backward_is_fine():
    x = Tensor([1.0, 2.0], requires_grad=True)
    y = x * x
    y.sum().backward()
    x[0] = 10.0  # graph already consumed; mutation is allowed
    np.testing.assert_allclose(x.grad, [2.0, 4.0])


def test_optimizer_step_bumps_version_and_is_detected():
    w = Parameter([1.0, 2.0], name="w")
    loss = (w * w).sum()
    opt = SGD([w], lr=0.1)
    loss.backward(retain_graph=True)
    opt.step()
    assert w._version == 1
    with pytest.raises(InplaceModificationError):
        loss.backward(retain_graph=True)


def test_fill_and_iadd_also_detected():
    for mutate in (lambda t: t.fill_(0.0), lambda t: t.__iadd__(1.0)):
        x = Tensor([1.0, 2.0], requires_grad=True)
        y = x * x
        mutate(x)
        with pytest.raises(InplaceModificationError):
            y.sum().backward()
