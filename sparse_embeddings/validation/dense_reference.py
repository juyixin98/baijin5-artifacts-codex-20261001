"""Independent dense reference implementation.

Everything here operates on dense ``(vocab_size, dim)`` arrays. Gradient
assembly uses an explicit Python accumulation loop (not the production
bincount path), clipping uses hand-written dense reductions, and the
optimizer update is expressed with a dense touched mask. The intentionally
different implementation makes it a genuine cross-check of the sparse kernels.

dtype mirroring: accumulation, scale division and clipping happen in float64
exactly as on the sparse path; the clipped touched gradient is then cast to
the table's declared storage dtype *once*, and momentum/weight arithmetic is
done in that dtype - the same boundary at which the sparse optimizer casts.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from sparse_embeddings.config import TableConfig


@dataclass
class DenseReferenceModel:
    """Dense mirror of the sparse optimizer semantics."""

    config: TableConfig
    weights: np.ndarray
    momentum: np.ndarray
    row_steps: np.ndarray
    global_step: int = 0

    @classmethod
    def from_config(
        cls, config: TableConfig, initial_weights: np.ndarray | None = None
    ) -> "DenseReferenceModel":
        if initial_weights is None:
            rng = np.random.default_rng(config.seed)
            initial_weights = rng.normal(
                0.0, 0.1, size=(config.vocab_size, config.dim)
            )
        storage = config.numpy_dtype
        return cls(
            config=config,
            weights=np.array(initial_weights, dtype=storage, copy=True),
            momentum=np.zeros((config.vocab_size, config.dim), dtype=storage),
            row_steps=np.zeros(config.vocab_size, dtype=np.int64),
        )

    def apply(
        self, indices: np.ndarray, values: np.ndarray, scale: float
    ) -> dict[str, np.ndarray | float | bool]:
        """Apply one raw batch the dense way. Empty batch => no step."""
        indices = np.asarray(indices, dtype=np.int64).reshape(-1)
        values = np.asarray(values, dtype=np.float64)
        if indices.size == 0:
            return {
                "stepped": False,
                "touched": np.empty(0, dtype=np.int64),
                "pre_norm": 0.0,
                "post_norm": 0.0,
                "clipped": False,
                "scales": np.empty(0, dtype=np.float64),
            }

        # Explicit float64 accumulation loop: independent of bincount path.
        dense_grad = np.zeros(
            (self.config.vocab_size, self.config.dim), dtype=np.float64
        )
        for token_idx, grad in zip(indices, values):
            dense_grad[int(token_idx)] += grad
        dense_grad /= scale
        touched = np.unique(indices)

        pre_norm = float(np.sqrt(np.sum(dense_grad[touched] ** 2)))
        clipped_f64, scales, clipped_flag = dense_clip(
            dense_grad, touched, self.config.clipping
        )
        post_norm = float(np.sqrt(np.sum(clipped_f64[touched] ** 2)))

        # Single cast at the storage boundary, mirroring the sparse path.
        storage = self.config.numpy_dtype
        clipped_storage = np.zeros_like(self.weights)
        clipped_storage[touched] = clipped_f64[touched].astype(storage)

        mask = np.zeros(self.config.vocab_size, dtype=bool)
        mask[touched] = True

        opt = self.config.optimizer
        if opt.name == "sgd":
            new_weights = self.weights.copy()
            new_weights[mask] = (
                self.weights[mask] - opt.learning_rate * clipped_storage[mask]
            )
            self.weights = new_weights
        else:
            new_momentum = self.momentum.copy()
            # v <- mu*v + g ONLY on touched rows; untouched buffers stay.
            new_momentum[mask] = (
                opt.momentum * self.momentum[mask] + clipped_storage[mask]
            )
            new_weights = self.weights.copy()
            new_weights[mask] = (
                self.weights[mask] - opt.learning_rate * new_momentum[mask]
            )
            self.weights = new_weights
            self.momentum = new_momentum

        self.row_steps[touched] += 1
        self.global_step += 1
        return {
            "stepped": True,
            "touched": touched,
            "pre_norm": pre_norm,
            "post_norm": post_norm,
            "clipped": bool(clipped_flag),
            "clipped_grad": clipped_storage,
            "scales": scales,
        }


def dense_clip(
    dense_grad: np.ndarray,
    touched: np.ndarray,
    clipping,
) -> tuple[np.ndarray, np.ndarray, bool]:
    """Dense clipping with strictly separated global/row branches."""
    if clipping is None:
        return dense_grad.copy(), np.ones(touched.size, dtype=np.float64), False

    out = dense_grad.copy()
    mode = clipping.mode
    max_norm = float(clipping.max_norm)

    if mode == "global":
        total = 0.0
        for i in touched:  # explicit loop, independent implementation
            total += float(np.sum(dense_grad[i] ** 2))
        norm = np.sqrt(total)
        if norm > max_norm and norm > 0.0:
            factor = max_norm / norm
            for i in touched:
                out[i] = dense_grad[i] * factor
            scales = np.full(touched.size, factor, dtype=np.float64)
            return out, scales, True
        return out, np.ones(touched.size, dtype=np.float64), False

    if mode == "row":
        scales = np.ones(touched.size, dtype=np.float64)
        applied = False
        for pos, i in enumerate(touched):
            row_norm = float(np.sqrt(np.sum(dense_grad[i] ** 2)))
            if row_norm > max_norm:
                factor = max_norm / row_norm
                out[i] = dense_grad[i] * factor
                scales[pos] = factor
                applied = True
        return out, scales, applied

    raise ValueError(f"unknown clipping mode {mode!r}")
