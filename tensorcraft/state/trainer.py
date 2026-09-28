"""Training state: a real, small SGD trainer built only on core ops.

The trainer fits a linear model ``y = X @ w + b`` with mean-squared loss.
Every forward/gradient/update quantity is a :class:`Tensor` and every
computation goes through :mod:`tensorcraft.tensor.ops` -- nothing about the
training loop is faked: the analytic gradients are the same expressions a
generic autodiff would derive here:

    pred      = X @ w + b
    residual  = pred - y
    loss      = sum(residual ** 2) / N
    grad_pred = 2 * residual / N
    grad_w    = transpose(X) @ grad_pred
    grad_b    = sum(grad_pred)

Lifecycle is an explicit state machine with illegal transitions rejected
by category ``STATE_ERROR``; checkpoints materialize independent copies.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..errors import StateError
from ..tensor import Tensor
from ..tensor import ops


class TrainingState(str, Enum):
    CREATED = "created"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


_LEGAL_TRANSITIONS: dict[TrainingState, frozenset[TrainingState]] = {
    TrainingState.CREATED: frozenset({TrainingState.READY, TrainingState.FAILED}),
    TrainingState.READY: frozenset({
        TrainingState.RUNNING, TrainingState.COMPLETED, TrainingState.FAILED}),
    TrainingState.RUNNING: frozenset({
        TrainingState.READY, TrainingState.PAUSED,
        TrainingState.COMPLETED, TrainingState.FAILED}),
    TrainingState.PAUSED: frozenset({
        TrainingState.RUNNING, TrainingState.FAILED}),
    TrainingState.COMPLETED: frozenset(),
    TrainingState.FAILED: frozenset({TrainingState.READY}),
}


@dataclass(frozen=True)
class Checkpoint:
    step: int
    loss: float
    weights: Tensor
    bias: Tensor

    def describe(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "loss": self.loss,
            "weights_storage_token": self.weights.token,
            "bias_storage_token": self.bias.token,
        }


@dataclass(frozen=True)
class StepReport:
    step: int
    loss_before: float
    loss_after: float
    grad_norm: float
    learning_rate: float
    weight_token_before: int
    weight_token_after: int
    copied: bool
    paused: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "loss_before": self.loss_before,
            "loss_after": self.loss_after,
            "grad_norm": self.grad_norm,
            "learning_rate": self.learning_rate,
            "weight_token_before": self.weight_token_before,
            "weight_token_after": self.weight_token_after,
            "copied": self.copied,
            "paused": self.paused,
        }


class LinearSGDTrainer:
    """Stateful trainer; parameters are readable as ordinary tensors."""

    def __init__(self, features: Tensor, targets: Tensor, *,
                 learning_rate: float = 0.05,
                 max_steps: int = 1000,
                 tol: float = 1e-10) -> None:
        self._validate_inputs(features, targets, learning_rate, max_steps)
        self._X = features
        self._y = targets
        self._n = features.shape[0]
        self._d = features.shape[1]
        self._lr = float(learning_rate)
        self._max_steps = int(max_steps)
        self._tol = float(tol)

        self._w = Tensor.zeros((self._d,), features.dtype.name)
        self._b = Tensor.zeros((1,), features.dtype.name)
        self._state = TrainingState.CREATED
        self._step = 0
        self._last_loss = self._compute_loss()
        self._pause_event = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_reports: list[StepReport] = []
        self._failure_reason: str | None = None
        self._transition(TrainingState.READY)

    # ---------------------------------------------------------------- #
    # Validation / state machine
    # ---------------------------------------------------------------- #

    @staticmethod
    def _validate_inputs(features, targets, learning_rate, max_steps) -> None:
        if not isinstance(features, Tensor) or not isinstance(targets, Tensor):
            raise TypeError("features and targets must be Tensors")
        if features.ndim != 2:
            raise StateError(
                f"features must be 2-D (N, D), got shape {features.shape}")
        if targets.ndim != 1:
            raise StateError(
                f"targets must be 1-D (N,), got shape {targets.shape}")
        if features.shape[0] != targets.shape[0]:
            raise StateError(
                f"feature rows {features.shape[0]} != targets {targets.shape[0]}")
        if features.shape[0] == 0:
            raise StateError("cannot train on an empty dataset (N=0)")
        if features.dtype.name != targets.dtype.name:
            raise StateError(
                f"features/targets dtype mismatch: {features.dtype.name} vs "
                f"{targets.dtype.name}")
        if features.dtype.kind != "float":
            raise StateError(
                f"SGD requires floating-point data, got dtype {features.dtype.name}")
        if (isinstance(learning_rate, bool)
                or not isinstance(learning_rate, (int, float))
                or not math.isfinite(learning_rate) or learning_rate <= 0):
            raise StateError(
                f"learning_rate must be a finite positive number, got {learning_rate!r}")
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) \
                or max_steps <= 0:
            raise StateError(
                f"max_steps must be a positive int, got {max_steps!r}")

    def _transition(self, target: TrainingState) -> None:
        allowed = _LEGAL_TRANSITIONS[self._state]
        if target not in allowed:
            raise StateError(
                f"illegal training transition {self._state.value} -> "
                f"{target.value}",
                details={"from": self._state.value, "to": target.value})
        self._state = target

    # ---------------------------------------------------------------- #
    # Real forward / gradient / update (all via core tensor ops)
    # ---------------------------------------------------------------- #

    def _forward(self) -> Tensor:
        # X (N,D) @ w (D,) -> (N,); bias (1,) broadcasts across rows.
        pred = ops.matmul(self._X, self._w).tensor
        return ops.elementwise(pred, self._b.broadcast_to((self._n,)),
                               "add").tensor

    def _compute_loss(self) -> float:
        pred = self._forward()
        residual = ops.elementwise(pred, self._y, "subtract").tensor
        squared = ops.elementwise(residual, residual, "multiply").tensor
        total = ops.reduce_sum(squared).tensor
        return float(total.to_numpy().reshape(()) / self._n)

    def _train_one_step(self) -> StepReport:
        loss_before = self._compute_loss()
        token_before = self._w.token

        pred = self._forward()
        residual = ops.elementwise(pred, self._y, "subtract").tensor
        grad_pred = ops.scalar_op(residual, 2.0 / self._n, "multiply").tensor

        # grad_w = X.T @ grad_pred  -- transpose is a zero-copy view and
        # matmul handles the non-contiguous operand directly.
        x_t = self._X.transpose()
        grad_w = ops.matmul(x_t, grad_pred).tensor
        grad_b_sum = ops.reduce_sum(grad_pred).tensor  # shape ()
        grad_b = grad_b_sum.reshape((1,))

        update_w = ops.scalar_op(grad_w, self._lr, "multiply").tensor
        new_w = ops.elementwise(self._w, update_w, "subtract").tensor
        update_b = ops.scalar_op(grad_b, self._lr, "multiply").tensor
        new_b = ops.elementwise(self._b, update_b, "subtract").tensor

        self._w = new_w
        self._b = new_b
        self._step += 1
        loss_after = self._compute_loss()
        self._last_loss = loss_after

        grad_norm = float(
            ops.elementwise(grad_w, grad_w, "multiply").tensor.to_numpy().sum()
            ** 0.5)
        return StepReport(
            step=self._step,
            loss_before=loss_before,
            loss_after=loss_after,
            grad_norm=grad_norm,
            learning_rate=self._lr,
            weight_token_before=token_before,
            weight_token_after=self._w.token,
            copied=True,
            paused=self._pause_event.is_set(),
        )

    # ---------------------------------------------------------------- #
    # Public lifecycle
    # ---------------------------------------------------------------- #

    def _validate_run_steps(self, steps: int) -> None:
        if isinstance(steps, bool) or not isinstance(steps, int) or steps <= 0:
            raise StateError(f"steps must be a positive int, got {steps!r}")

    def _train_loop(self, steps: int) -> list[StepReport]:
        """Run up to ``steps`` steps, honouring the pause event.

        Caller must hold the state-machine precondition; the lock guards
        parameter state so concurrent readers (status/weights) always see a
        consistent snapshot.
        """
        reports: list[StepReport] = []
        for _ in range(steps):
            if self._pause_event.is_set():
                self._transition(TrainingState.PAUSED)
                return reports
            with self._lock:
                report = self._train_one_step()
            reports.append(report)
            if report.loss_after <= self._tol:
                self._transition(TrainingState.COMPLETED)
                return reports
            if self._step >= self._max_steps:
                self._transition(TrainingState.COMPLETED)
                return reports
        self._transition(TrainingState.READY)
        return reports

    def run(self, steps: int) -> list[StepReport]:
        """Synchronously run training steps (blocking)."""
        self._validate_run_steps(steps)
        with self._lock:
            if self._state not in (TrainingState.READY, TrainingState.PAUSED):
                raise StateError(
                    f"cannot run from state {self._state.value}; expected "
                    "ready/paused")
            self._pause_event.clear()
            self._transition(TrainingState.RUNNING)
        try:
            reports = self._train_loop(steps)
        except Exception as exc:
            self._failure_reason = str(exc)
            if self._state is not TrainingState.FAILED:
                self._transition(TrainingState.FAILED)
            raise
        self._last_reports = reports
        return reports

    def run_background(self, steps: int) -> None:
        """Start training on a daemon thread so pause/status can interleave."""
        self._validate_run_steps(steps)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise StateError("a training run is already active")
            if self._state not in (TrainingState.READY, TrainingState.PAUSED):
                raise StateError(
                    f"cannot run from state {self._state.value}; expected "
                    "ready/paused")
            self._pause_event.clear()
            self._transition(TrainingState.RUNNING)

        def worker() -> None:
            try:
                self._last_reports = self._train_loop(steps)
            except Exception as exc:  # recorded on the session, not swallowed
                self._failure_reason = str(exc)
                with self._lock:
                    if self._state is not TrainingState.FAILED:
                        self._state = TrainingState.FAILED

        thread = threading.Thread(
            target=worker, name=f"sgd-{id(self):x}", daemon=True)
        self._thread = thread
        thread.start()

    def request_pause(self) -> None:
        """Request the background loop to pause at the next step boundary."""
        if self._state is not TrainingState.RUNNING:
            raise StateError(
                f"pause requested outside a run (state={self._state.value})")
        self._pause_event.set()

    def resume(self) -> None:
        """Clear a pause; valid only from the paused state."""
        with self._lock:
            if self._state is not TrainingState.PAUSED:
                raise StateError(
                    f"resume requires paused state, got {self._state.value}")
            self._pause_event.clear()
            self._transition(TrainingState.RUNNING)

        def worker() -> None:
            # Continue until the configured step budget is exhausted.
            remaining = max(1, self._max_steps - self._step)
            try:
                self._last_reports = self._train_loop(remaining)
            except Exception as exc:
                self._failure_reason = str(exc)
                with self._lock:
                    if self._state is not TrainingState.FAILED:
                        self._state = TrainingState.FAILED

        thread = threading.Thread(
            target=worker, name=f"sgd-resume-{id(self):x}", daemon=True)
        self._thread = thread
        thread.start()

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until no background step is active. Returns completion."""
        if self._thread is not None:
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True

    def reset_failure(self) -> None:
        if self._state is not TrainingState.FAILED:
            raise StateError("reset_failure requires failed state")
        self._failure_reason = None
        self._transition(TrainingState.READY)

    def checkpoint(self) -> Checkpoint:
        if self._state not in (
                TrainingState.READY, TrainingState.PAUSED,
                TrainingState.COMPLETED, TrainingState.FAILED):
            raise StateError(
                f"cannot checkpoint in state {self._state.value}")
        # Independent materialized copies: restoring can never alias live
        # training parameters.
        return Checkpoint(
            step=self._step,
            loss=self._last_loss,
            weights=self._w.materialize(),
            bias=self._b.materialize(),
        )

    def restore(self, checkpoint: Checkpoint) -> None:
        if self._state not in (TrainingState.READY, TrainingState.PAUSED,
                               TrainingState.FAILED):
            raise StateError(
                f"cannot restore in state {self._state.value}")
        self._w = checkpoint.weights.materialize()
        self._b = checkpoint.bias.materialize()
        self._step = checkpoint.step
        self._last_loss = checkpoint.loss

    # ---------------------------------------------------------------- #
    # Read-only views
    # ---------------------------------------------------------------- #

    @property
    def state(self) -> TrainingState:
        return self._state

    @property
    def step(self) -> int:
        return self._step

    @property
    def last_loss(self) -> float:
        return self._last_loss

    @property
    def failure_reason(self) -> str | None:
        return self._failure_reason

    @property
    def weights(self) -> Tensor:
        return self._w

    @property
    def bias(self) -> Tensor:
        return self._b

    @property
    def features(self) -> Tensor:
        return self._X

    @property
    def targets(self) -> Tensor:
        return self._y

    def predict(self) -> Tensor:
        if self._state not in (
                TrainingState.READY, TrainingState.PAUSED,
                TrainingState.COMPLETED):
            raise StateError(
                f"cannot predict in state {self._state.value}")
        return self._forward()

    def describe(self) -> dict[str, Any]:
        return {
            "state": self._state.value,
            "step": self._step,
            "last_loss": self._last_loss,
            "learning_rate": self._lr,
            "max_steps": self._max_steps,
            "n": self._n,
            "d": self._d,
            "weights_token": self._w.token,
            "failure_reason": self._failure_reason,
        }
