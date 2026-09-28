"""Layer 3: training state.

:class:`TableState` owns the parameters, momentum buffer and per-row step
counters of one embedding table. :class:`SparseOptimizerService` owns the
table registry and orchestrates one batch end to end:

    validate -> aggregate duplicates -> clip (declared mode) -> optimizer step
             -> finite-check candidate -> persist (atomic) -> swap live state

The transaction boundary is deliberate. Kernels first produce *candidate*
arrays; nothing live is mutated. The persistence hook then writes the
candidate via a temp-file + fsync + atomic rename. Only after that succeeds
are the live references swapped. Consequently:

* a rejected batch (bad index, non-finite value, ...) changes nothing;
* a persistence failure changes neither live state nor the previous
  checkpoint;
* only rows actually touched appear in the checkpoint.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, TYPE_CHECKING

import numpy as np

from sparse_embeddings.config import TableConfig
from sparse_embeddings.graph import (
    StepRules,
    aggregate_duplicate_indices,
    clip_gradients,
    momentum_sgd_step,
    sgd_step,
)
from sparse_embeddings.observability import NullLogger, StructuredLogger, new_run_id
from sparse_embeddings.tensor_types import (
    ErrorCategory,
    SparseEmbeddingError,
    SparseGradientBatch,
)

if TYPE_CHECKING:
    from sparse_embeddings.persistence import CheckpointStore


@dataclass(frozen=True)
class StepResult:
    """Structured outcome of one batch - the decision basis, not just ok/fail."""

    run_id: str
    table: str
    stepped: bool
    reason: str
    global_step_before: int
    global_step_after: int
    nnz: int
    n_unique_touched: int
    touched_indices: np.ndarray
    token_counts: np.ndarray
    zero_gradient_rows: np.ndarray
    clip_mode: str | None
    pre_clip_global_norm: float
    post_clip_global_norm: float
    clip_applied: bool
    per_row_scales: np.ndarray

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "table": self.table,
            "stepped": self.stepped,
            "reason": self.reason,
            "global_step_before": self.global_step_before,
            "global_step_after": self.global_step_after,
            "nnz": self.nnz,
            "n_unique_touched": self.n_unique_touched,
            "touched_indices": self.touched_indices,
            "token_counts": self.token_counts,
            "zero_gradient_rows": self.zero_gradient_rows,
            "clip_mode": self.clip_mode,
            "pre_clip_global_norm": self.pre_clip_global_norm,
            "post_clip_global_norm": self.post_clip_global_norm,
            "clip_applied": self.clip_applied,
            "per_row_scales": self.per_row_scales,
        }


@dataclass(frozen=True)
class PreparedStep:
    """Candidate next state plus its audit record. Not yet visible to readers."""

    result: StepResult
    cand_weights: np.ndarray
    cand_momentum: np.ndarray
    cand_row_steps: np.ndarray
    cand_ever_touched: np.ndarray


# Signature of the write-ahead persistence hook (see SparseOptimizerService).
PersistHook = Callable[["TableState", PreparedStep], None]


class TableState:
    """State of one embedding table; mutated only through prepare+commit."""

    def __init__(self, config: TableConfig) -> None:
        self._config = config
        rng = np.random.default_rng(config.seed)
        dtype = config.numpy_dtype
        # Deterministic small initialization in the declared storage dtype.
        self._weights = rng.normal(0.0, 0.1, size=(config.vocab_size, config.dim)).astype(
            dtype
        )
        self._momentum = np.zeros_like(self._weights)
        self._row_steps = np.zeros(config.vocab_size, dtype=np.int64)
        self._global_step = 0
        self._ever_touched = np.zeros(config.vocab_size, dtype=bool)
        self._lock = threading.Lock()

    @property
    def config(self) -> TableConfig:
        return self._config

    @property
    def weights(self) -> np.ndarray:
        return self._weights

    @property
    def momentum(self) -> np.ndarray:
        return self._momentum

    @property
    def row_steps(self) -> np.ndarray:
        return self._row_steps

    @property
    def global_step(self) -> int:
        return self._global_step

    @property
    def ever_touched(self) -> np.ndarray:
        return self._ever_touched

    def snapshot_touched(
        self,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Return ``(indices, weight rows, momentum rows, row steps)`` for touched rows."""
        idx = np.nonzero(self._ever_touched)[0].astype(np.int64)
        return (
            idx,
            self._weights[idx].copy(),
            self._momentum[idx].copy(),
            self._row_steps[idx].copy(),
        )

    def apply_batch(
        self,
        batch: SparseGradientBatch,
        *,
        run_id: str,
        rules: StepRules,
        persist_hook: PersistHook | None = None,
    ) -> StepResult:
        """Prepare, optionally durably persist the candidate, then swap live state.

        The hook runs while the table lock is held and *before* the swap. A
        hook exception aborts the step: candidates are discarded and the
        previous state stays live.
        """
        with self._lock:
            prepared = self._prepare(batch, run_id=run_id, rules=rules)
            result = prepared.result
            if not result.stepped:
                return result
            if persist_hook is not None:
                persist_hook(self, prepared)  # raises -> no commit below
            self._commit(prepared)
            return result

    def _prepare(
        self, batch: SparseGradientBatch, *, run_id: str, rules: StepRules
    ) -> PreparedStep:
        step_before = self._global_step
        if batch.is_empty:
            if rules.step_on_empty_batch:
                raise SparseEmbeddingError(
                    ErrorCategory.CONFIG_ERROR,
                    "step_on_empty_batch=True is not supported",
                )
            result = StepResult(
                run_id=run_id,
                table=self._config.name,
                stepped=False,
                reason="empty_batch_no_step",
                global_step_before=step_before,
                global_step_after=step_before,
                nnz=0,
                n_unique_touched=0,
                touched_indices=np.empty(0, dtype=np.int64),
                token_counts=np.empty(0, dtype=np.int64),
                zero_gradient_rows=np.empty(0, dtype=np.int64),
                clip_mode=self._config.clipping.mode if self._config.clipping else None,
                pre_clip_global_norm=0.0,
                post_clip_global_norm=0.0,
                clip_applied=False,
                per_row_scales=np.empty(0, dtype=np.float64),
            )
            return PreparedStep(
                result=result,
                cand_weights=self._weights,
                cand_momentum=self._momentum,
                cand_row_steps=self._row_steps,
                cand_ever_touched=self._ever_touched,
            )

        touched, aggregated, counts = aggregate_duplicate_indices(
            batch.indices, batch.values, scale=batch.scale, dim=self._config.dim
        )
        clip = clip_gradients(aggregated, self._config.clipping)

        # Cast the applied gradient to storage dtype at the boundary so the
        # sparse path and a dense loop over the same dtype round identically.
        grad_storage = clip.gradients.astype(self._config.numpy_dtype)
        opt = self._config.optimizer
        if opt.name == "sgd":
            cand_w = sgd_step(
                self._weights, touched, grad_storage, learning_rate=opt.learning_rate
            )
            cand_m = self._momentum
        else:
            cand_w, cand_m = momentum_sgd_step(
                self._weights,
                self._momentum,
                touched,
                grad_storage,
                learning_rate=opt.learning_rate,
                momentum=opt.momentum,
            )

        if not np.all(np.isfinite(cand_w[touched])) or not np.all(
            np.isfinite(cand_m[touched])
        ):
            raise SparseEmbeddingError(
                ErrorCategory.NUMERIC_ERROR,
                "optimizer step produced non-finite weights or momentum; "
                "state left unchanged",
                details={"run_id": run_id, "touched": int(touched.size)},
            )

        zero_rows = touched[np.all(aggregated == 0.0, axis=1)]
        # A touched zero-gradient row still consumes a step under the policy;
        # counters make that observable (momentum decays, w moves only if the
        # buffer was non-zero).
        cand_steps = self._row_steps.copy()
        cand_steps[touched] += 1
        cand_touched = self._ever_touched.copy()
        cand_touched[touched] = True

        post_norm = float(np.sqrt(np.sum(clip.gradients * clip.gradients)))
        result = StepResult(
            run_id=run_id,
            table=self._config.name,
            stepped=True,
            reason="applied",
            global_step_before=step_before,
            global_step_after=step_before + 1,
            nnz=batch.nnz,
            n_unique_touched=int(touched.size),
            touched_indices=touched,
            token_counts=counts,
            zero_gradient_rows=zero_rows,
            clip_mode=self._config.clipping.mode if self._config.clipping else None,
            pre_clip_global_norm=clip.pre_norm,
            post_clip_global_norm=post_norm,
            clip_applied=clip.clipped,
            per_row_scales=clip.scales,
        )
        return PreparedStep(
            result=result,
            cand_weights=cand_w,
            cand_momentum=cand_m,
            cand_row_steps=cand_steps,
            cand_ever_touched=cand_touched,
        )

    def _commit(self, prepared: PreparedStep) -> None:
        self._weights = prepared.cand_weights
        self._momentum = prepared.cand_momentum
        self._row_steps = prepared.cand_row_steps
        self._ever_touched = prepared.cand_ever_touched
        self._global_step = prepared.result.global_step_after

    def restore_touched(
        self,
        indices: np.ndarray,
        weight_rows: np.ndarray,
        momentum_rows: np.ndarray,
        row_steps: np.ndarray,
        global_step: int,
    ) -> None:
        """Overwrite only the persisted (previously touched) rows."""
        with self._lock:
            self._weights[indices] = weight_rows.astype(self._weights.dtype)
            self._momentum[indices] = momentum_rows.astype(self._momentum.dtype)
            self._row_steps[indices] = row_steps
            self._ever_touched[indices] = True
            self._global_step = global_step


class SparseOptimizerService:
    """Registry + transactional orchestration around :class:`TableState`."""

    def __init__(
        self,
        *,
        logger: StructuredLogger | None = None,
        rules: StepRules | None = None,
        store: "CheckpointStore | None" = None,
    ) -> None:
        self._tables: dict[str, TableState] = {}
        self._logger = logger or NullLogger()
        self._rules = rules or StepRules()
        self._store = store

    @property
    def logger(self) -> StructuredLogger:
        return self._logger

    @property
    def rules(self) -> StepRules:
        return self._rules

    def create_table(self, config: TableConfig) -> TableState:
        if config.name in self._tables:
            raise SparseEmbeddingError(
                ErrorCategory.STATE_CONFLICT,
                f"table {config.name!r} already exists",
                details={"table": config.name},
            )
        table = TableState(config)
        self._tables[config.name] = table
        self._logger.event(
            "table_created",
            verdict="created",
            table=config.name,
            vocab_size=config.vocab_size,
            dim=config.dim,
            optimizer=config.optimizer.name,
            clip_mode=config.clipping.mode if config.clipping else None,
        )
        return table

    def get_table(self, name: str) -> TableState:
        table = self._tables.get(name)
        if table is None:
            raise SparseEmbeddingError(
                ErrorCategory.NOT_FOUND,
                f"table {name!r} does not exist",
                details={"table": name},
            )
        return table

    def apply_batch(
        self,
        table_name: str,
        batch: SparseGradientBatch,
        *,
        run_id: str | None = None,
    ) -> StepResult:
        run_id = run_id or new_run_id()
        table = self.get_table(table_name)
        self._logger.event(
            "batch_received",
            run_id=run_id,
            verdict="processing",
            table=table_name,
            nnz=batch.nnz,
            scale=batch.scale,
            input_indices=batch.indices,
        )

        hook = None
        if self._store is not None:
            hook = self._store.commit_prepared

        try:
            result = table.apply_batch(
                batch, run_id=run_id, rules=self._rules, persist_hook=hook
            )
        except SparseEmbeddingError as exc:
            self._logger.event(
                "batch_rejected",
                run_id=run_id,
                verdict="rejected",
                table=table_name,
                error=exc.to_dict(),
            )
            raise

        fields = result.to_log_dict()
        fields.pop("run_id", None)  # passed explicitly below
        self._logger.event(
            "batch_applied" if result.stepped else "batch_skipped",
            run_id=run_id,
            verdict="applied" if result.stepped else "skipped",
            **fields,
        )
        return result
