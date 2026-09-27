"""Tests for training state: parameter bundle, SGD, modes."""
from __future__ import annotations

import numpy as np
import pytest

from autodiff import Tensor, backward, ops
from autodiff.training import (
    Mode,
    TrainingState,
    ParameterBundle,
    SGD,
    SGDConfig,
)


def make_quadratic():
    w = Tensor(np.array([1.0, -2.0]), requires_grad=True)
    b = Tensor(np.array([0.5]), requires_grad=True)
    x = Tensor(np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]]))
    y = Tensor(np.array([1.0, 1.0, 2.0]))
    pred = ops.matmul(x, w) + b
    loss = ((pred - y) * (pred - y)).sum()
    return w, b, loss


def test_bundle_rejects_non_leaf_parameters():
    x = Tensor(np.ones((2,)), requires_grad=True)
    out = (x * x).sum()
    with pytest.raises(ValueError):
        ParameterBundle(w=out)


def test_sgd_descends_a_concrete_quadratic():
    w, b, loss = make_quadratic()
    params = ParameterBundle(w=w, b=b)
    opt = SGD(params, SGDConfig(lr=0.05))
    params.zero_grads()
    backward(loss)
    initial = float(loss.data)
    outcomes = opt.step()
    assert outcomes == {"w": "updated", "b": "updated"}
    # Rebuild the graph at the new point and confirm loss decreased.
    x = Tensor(np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]]))
    y = Tensor(np.array([1.0, 1.0, 2.0]))
    pred2 = ops.matmul(x, w) + b
    loss2 = float(((pred2 - y) * (pred2 - y)).sum().data)
    assert loss2 < initial


def test_sgd_skips_parameters_with_no_gradient():
    used = Tensor(np.array([1.0]), requires_grad=True)
    ignored = Tensor(np.array([7.0]), requires_grad=True)
    bundle = ParameterBundle(used=used, ignored=ignored)
    backward((used * used).sum())
    opt = SGD(bundle, SGDConfig(lr=0.1))
    outcomes = opt.step()
    assert outcomes == {"used": "updated", "ignored": "skipped-no-grad"}
    # The skipped parameter is untouched, not silently moved by a zero step.
    assert ignored.data.tolist() == [7.0]
    assert bundle.missing_gradients() == ["ignored"]


def test_zero_vs_missing_gradient_reporting():
    reached_zero = Tensor(np.array([1.0]), requires_grad=True)
    missing = Tensor(np.array([1.0]), requires_grad=True)
    bundle = ParameterBundle(a=reached_zero, b=missing)
    backward((reached_zero * 0.0).sum())
    assert bundle.zero_gradients() == ["a"]
    assert bundle.missing_gradients() == ["b"]


def test_training_mode_transitions_and_steps():
    state = TrainingState()
    assert state.is_training()
    assert state.step == 0
    state.eval()
    assert state.mode is Mode.EVAL
    state.increment_step()
    state.train()
    assert state.is_training()
    assert state.snapshot() == {"mode": "train", "step": 1}


def test_optimizer_update_invalidates_old_graph():
    w = Tensor(np.array([1.0]), requires_grad=True)
    bundle = ParameterBundle(w=w)
    loss = (w * w).sum()
    backward(loss)  # releases graph
    SGD(bundle, SGDConfig(lr=0.1)).step()
    assert w.version == 1  # update bumped the version
    assert w.data.tolist() == pytest.approx([0.8])
