"""Persistence: write-ahead, touched-state-only checkpoints.

For every stepping batch the service invokes :meth:`CheckpointStore.commit_prepared`
while holding the table lock and *before* live state is swapped::

    1. export only rows ever touched from the *candidate* arrays
    2. write to a temp file in the checkpoint directory
    3. flush + fsync
    4. os.replace() onto the live checkpoint path  <- single commit point
    5. fsync the directory

Any error (including a simulated disk fault in tests) raises before the
in-memory swap, so both the previous checkpoint and live state survive
unchanged. Rows never touched are not stored - their initialization is
reproducible from the config seed - keeping checkpoint size proportional to
touched state rather than vocabulary size.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from sparse_embeddings.config import (
    ClippingConfig,
    OptimizerConfig,
    TableConfig,
)
from sparse_embeddings.observability import StructuredLogger
from sparse_embeddings.state import PreparedStep, TableState
from sparse_embeddings.tensor_types import ErrorCategory, SparseEmbeddingError


class CheckpointStore:
    """One ``.npz`` checkpoint per table, committed via atomic temp rename."""

    def __init__(self, directory: Path | str) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def directory(self) -> Path:
        return self._dir

    def path_for(self, table_name: str) -> Path:
        return self._dir / f"{table_name}.npz"

    def commit_prepared(
        self,
        table: TableState,
        prepared: PreparedStep,
        logger: StructuredLogger | None = None,
    ) -> Path:
        """Durably persist the candidate state atomically, pre-swap.

        Implements the ``PersistHook`` signature; an exception here aborts the
        step before live state changes.
        """
        cfg = table.config
        result = prepared.result
        target = self.path_for(cfg.name)

        touched_idx = np.nonzero(prepared.cand_ever_touched)[0].astype(np.int64)
        payload: dict[str, np.ndarray] = {
            "touched_indices": touched_idx,
            "weight_rows": prepared.cand_weights[touched_idx]
            .astype(np.float64)
            .copy(),
            "momentum_rows": prepared.cand_momentum[touched_idx]
            .astype(np.float64)
            .copy(),
            "row_steps": prepared.cand_row_steps[touched_idx].copy(),
        }
        meta = {
            "table": cfg.name,
            "vocab_size": cfg.vocab_size,
            "dim": cfg.dim,
            "dtype": cfg.dtype,
            "seed": cfg.seed,
            "optimizer_name": cfg.optimizer.name,
            "learning_rate": cfg.optimizer.learning_rate,
            "momentum": cfg.optimizer.momentum,
            "has_clipping": cfg.clipping is not None,
            "clip_mode": cfg.clipping.mode if cfg.clipping else "",
            "clip_max_norm": cfg.clipping.max_norm if cfg.clipping else 0.0,
            "global_step": result.global_step_after,
            "run_id": result.run_id,
        }

        # Temp file in the same directory so os.replace is atomic.
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{cfg.name}.", suffix=".npz.tmp", dir=self._dir
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                np.savez(fh, **payload, meta=np.array(json.dumps(meta, sort_keys=True)))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, target)  # commit point
            _fsync_dir(self._dir)
        except (OSError, ValueError) as exc:
            tmp_path.unlink(missing_ok=True)
            raise SparseEmbeddingError(
                ErrorCategory.PERSISTENCE_ERROR,
                f"checkpoint commit failed for {cfg.name!r}: {exc}",
                details={"path": str(target), "run_id": result.run_id},
            ) from exc

        if logger is not None:
            logger.event(
                "checkpoint_committed",
                run_id=result.run_id,
                verdict="committed",
                table=cfg.name,
                path=str(target),
                rows_written=int(touched_idx.size),
                global_step=result.global_step_after,
            )
        return target

    def load(
        self, table_name: str
    ) -> tuple[TableConfig, dict[str, np.ndarray], int, str]:
        """Read a checkpoint without mutating any service state."""
        path = self.path_for(table_name)
        if not path.exists():
            raise SparseEmbeddingError(
                ErrorCategory.NOT_FOUND,
                f"no checkpoint for table {table_name!r} at {path}",
                details={"path": str(path)},
            )
        try:
            with np.load(path, allow_pickle=True) as data:
                meta = json.loads(str(data["meta"].item()))
                arrays = {
                    "touched_indices": data["touched_indices"].astype(np.int64),
                    "weight_rows": data["weight_rows"].astype(np.float64),
                    "momentum_rows": data["momentum_rows"].astype(np.float64),
                    "row_steps": data["row_steps"].astype(np.int64),
                }
        except (OSError, ValueError, KeyError) as exc:
            raise SparseEmbeddingError(
                ErrorCategory.PERSISTENCE_ERROR,
                f"checkpoint for {table_name!r} is unreadable: {exc}",
                details={"path": str(path)},
            ) from exc

        clip = None
        if meta["has_clipping"]:
            clip = ClippingConfig(
                mode=meta["clip_mode"], max_norm=float(meta["clip_max_norm"])
            )
        config = TableConfig(
            name=meta["table"],
            vocab_size=int(meta["vocab_size"]),
            dim=int(meta["dim"]),
            optimizer=OptimizerConfig(
                name=meta["optimizer_name"],
                learning_rate=float(meta["learning_rate"]),
                momentum=float(meta["momentum"]),
            ),
            clipping=clip,
            dtype=meta["dtype"],
            seed=int(meta["seed"]),
        )
        return config, arrays, int(meta["global_step"]), str(meta["run_id"])


def restore_into(service: Any, table_name: str, store: CheckpointStore) -> TableState:
    """Recreate a table from its checkpoint, overwriting only touched rows."""
    config, arrays, global_step, _run_id = store.load(table_name)
    if table_name in service._tables:  # noqa: SLF001 - same-package restore
        raise SparseEmbeddingError(
            ErrorCategory.STATE_CONFLICT,
            f"table {table_name!r} already exists; refusing to restore over live state",
        )
    table = service.create_table(config)
    idx = arrays["touched_indices"]
    table.restore_touched(
        idx,
        arrays["weight_rows"],
        arrays["momentum_rows"],
        arrays["row_steps"],
        global_step,
    )
    return table


def _fsync_dir(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
