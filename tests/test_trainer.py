"""Tests for the SGD trainer: real convergence, state machine, checkpoints."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from tensorcraft.state import (
    LinearSGDTrainer,
    TrainingState,
)
from tensorcraft.tensor import Tensor


@pytest.fixture
def regression_problem():
    rng = np.random.default_rng(42)
    n, d = 400, 4
    X = rng.normal(size=(n, d))
    true_w = np.array([1.5, -2.0, 0.5, 0.25])
    true_b = -0.75
    y = X @ true_w + true_b + rng.normal(scale=1e-3, size=n)
    features = Tensor.from_nested(X, "float64")
    targets = Tensor.from_nested(y, "float64")
    return features, targets, true_w, true_b


class TestConvergence:
    def test_recovers_true_parameters(self, regression_problem):
        X, y, true_w, true_b = regression_problem
        trainer = LinearSGDTrainer(
            X, y, learning_rate=0.05, max_steps=500)
        trainer.run(300)
        assert trainer.state is TrainingState.READY or \
            trainer.state is TrainingState.COMPLETED
        np.testing.assert_allclose(
            trainer.weights.to_numpy(), true_w, atol=1e-2)
        np.testing.assert_allclose(
            trainer.bias.to_numpy(), [true_b], atol=1e-2)

    def test_loss_decreases_monotonically_on_batch_gd(self, regression_problem):
        X, y, _, _ = regression_problem
        trainer = LinearSGDTrainer(X, y, learning_rate=0.05, max_steps=1000)
        reports = trainer.run(50)
        losses = [report.loss_after for report in reports]
        # Full-batch GD with this learning rate is non-increasing after step 1.
        assert all(later <= earlier + 1e-12
                   for earlier, later in zip(losses, losses[1:]))
        assert reports[-1].loss_after < reports[0].loss_before

    def test_predictions_use_trained_params(self, regression_problem):
        X, y, true_w, true_b = regression_problem
        trainer = LinearSGDTrainer(X, y, learning_rate=0.05, max_steps=300)
        trainer.run(200)
        pred = trainer.predict().to_numpy()
        rmse = float(np.sqrt(np.mean((pred - y.to_numpy()) ** 2)))
        assert rmse < 1e-2

    def test_every_step_allocates_fresh_weight_storage(self, regression_problem):
        X, y, _, _ = regression_problem
        trainer = LinearSGDTrainer(X, y, learning_rate=0.05, max_steps=10)
        reports = trainer.run(3)
        # Each SGD update builds new tensors through ops (immutability).
        tokens = [reports[0].weight_token_before,
                  *[r.weight_token_after for r in reports]]
        assert len(set(tokens)) == len(tokens)


class TestStateMachine:
    def test_starts_ready(self, regression_problem):
        trainer = LinearSGDTrainer(*regression_problem[:2])
        assert trainer.state is TrainingState.READY

    def test_completed_does_not_accept_more_steps(self, regression_problem):
        trainer = LinearSGDTrainer(
            *regression_problem[:2], learning_rate=0.5, max_steps=5, tol=1e12)
        trainer.run(5)  # huge tol -> completes immediately
        assert trainer.state is TrainingState.COMPLETED
        with pytest.raises(Exception) as exc:
            trainer.run(1)
        assert exc.value.category == "STATE_ERROR"

    def test_invalid_steps_rejected(self, regression_problem):
        trainer = LinearSGDTrainer(*regression_problem[:2])
        with pytest.raises(Exception):
            trainer.run(0)
        with pytest.raises(Exception):
            trainer.run(-3)

    def test_pause_resume_background(self, regression_problem):
        trainer = LinearSGDTrainer(
            *regression_problem[:2], learning_rate=0.01, max_steps=100_000)
        trainer.run_background(100_000)
        assert trainer.state is TrainingState.RUNNING
        trainer.request_pause()
        trainer.wait_idle(timeout=10)
        assert trainer.state is TrainingState.PAUSED
        paused_step = trainer.step
        trainer.resume()
        time.sleep(0.2)
        trainer.request_pause()
        trainer.wait_idle(timeout=10)
        assert trainer.step > paused_step

    def test_pause_only_while_running(self, regression_problem):
        trainer = LinearSGDTrainer(*regression_problem[:2])
        with pytest.raises(Exception):
            trainer.request_pause()

    def test_concurrent_run_rejected(self, regression_problem):
        trainer = LinearSGDTrainer(
            *regression_problem[:2], learning_rate=0.01, max_steps=100_000)
        trainer.run_background(100_000)
        try:
            with pytest.raises(Exception):
                trainer.run_background(10)
        finally:
            trainer.request_pause()
            trainer.wait_idle(10)


class TestCheckpoints:
    def test_checkpoint_independent_and_restorable(self, regression_problem):
        X, y, _, _ = regression_problem
        trainer = LinearSGDTrainer(X, y, learning_rate=0.05)
        trainer.run(20)
        ckpt = trainer.checkpoint()
        assert ckpt.weights.token != trainer.weights.token
        assert not ckpt.weights.shares_storage_with(trainer.weights)

        trainer.run(30)
        later_loss = trainer.last_loss
        trainer.restore(ckpt)
        assert trainer.step == ckpt.step
        assert trainer.last_loss == ckpt.loss
        assert trainer.last_loss != later_loss

    def test_restore_makes_independent_copy(self, regression_problem):
        X, y, _, _ = regression_problem
        trainer = LinearSGDTrainer(X, y, learning_rate=0.05)
        trainer.run(10)
        ckpt = trainer.checkpoint()
        trainer.restore(ckpt)
        # Mutating restored params must not mutate the checkpoint.
        assert not trainer.weights.shares_storage_with(ckpt.weights)


class TestValidation:
    def test_requires_2d_features(self):
        X = Tensor.from_nested([1.0, 2.0], "float64")
        y = Tensor.from_nested([1.0, 2.0], "float64")
        with pytest.raises(Exception) as exc:
            LinearSGDTrainer(X, y)
        assert exc.value.category == "STATE_ERROR"

    def test_rejects_empty_dataset(self):
        X = Tensor.from_nested(np.zeros((0, 2)).tolist(), "float64")
        y = Tensor.from_nested([], "float64")
        with pytest.raises(Exception):
            LinearSGDTrainer(X, y)

    def test_rejects_integer_data(self):
        X = Tensor.from_nested(np.ones((3, 2)).tolist(), "int64")
        y = Tensor.from_nested([1, 1, 1], "int64")
        with pytest.raises(Exception):
            LinearSGDTrainer(X, y)

    def test_rejects_bad_learning_rate(self, regression_problem):
        with pytest.raises(Exception):
            LinearSGDTrainer(*regression_problem[:2], learning_rate=0)
        with pytest.raises(Exception):
            LinearSGDTrainer(*regression_problem[:2], learning_rate=float("nan"))
