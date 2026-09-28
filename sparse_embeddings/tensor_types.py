"""Layer 1: tensor types.

Validated sparse tensor type ``SparseGradientBatch`` plus the error taxonomy.

A sparse gradient batch is the COO-like triple::

    indices : (nnz,)      int64 row ids into [0, vocab_size)
    values  : (nnz, dim)  float per-token gradient contributions
    scale   : scalar      divisor applied while aggregating (e.g. token count)

Validation is *all-or-nothing*: any out-of-range index, NaN/Inf value, shape
mismatch, or wrong dtype rejects the **whole batch** with a typed error before
aggregation touches table state. Partial application never happens.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np


class ErrorCategory(str, Enum):
    """Stable failure categories reported on the API and in logs.

    Unknown exceptions are mapped to ``INTERNAL_ERROR`` rather than reported as
    success; callers can branch on these values.
    """

    CONFIG_ERROR = "config_error"
    VALIDATION_ERROR = "validation_error"
    INDEX_OUT_OF_RANGE = "index_out_of_range"
    NUMERIC_ERROR = "numeric_error"
    NOT_FOUND = "not_found"
    STATE_CONFLICT = "state_conflict"
    PERSISTENCE_ERROR = "persistence_error"
    EMPTY_BATCH = "empty_batch"
    INTERNAL_ERROR = "internal_error"


class SparseEmbeddingError(Exception):
    """Typed domain error. Never logged/returned as a generic success."""

    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True)
class SparseGradientBatch:
    """Immutable, validated sparse gradient batch.

    Instances can only be created through :meth:`from_lists`, which performs
    full validation. ``indices`` and ``values`` are copied and made contiguous
    so callers cannot mutate validated state through a retained array.
    """

    indices: np.ndarray  # shape (nnz,), int64
    values: np.ndarray  # shape (nnz, dim), float
    scale: float

    @property
    def nnz(self) -> int:
        return int(self.indices.shape[0])

    @property
    def dim(self) -> int:
        return int(self.values.shape[1])

    @property
    def is_empty(self) -> bool:
        return self.nnz == 0

    @classmethod
    def from_lists(
        cls,
        indices: Any,
        values: Any,
        *,
        vocab_size: int,
        expected_dim: int,
        scale: float | None = None,
    ) -> "SparseGradientBatch":
        """Validate a raw (indices, values) pair for one table.

        ``scale`` defaults to the token count ``nnz`` (mean-over-tokens
        reduction) but may be passed explicitly. The whole batch is rejected
        on the first problem found; validation order is fixed so error
        categories are deterministic:

        1. structural (shape / rank / dtype / scale)
        2. non-finite values                -> NUMERIC_ERROR
        3. index integrality                -> VALIDATION_ERROR
        4. index range                      -> INDEX_OUT_OF_RANGE
        """
        idx = _as_1d_int_array(indices)
        val = _as_2d_float_array(values, expected_dim=expected_dim)

        if idx.shape[0] != val.shape[0]:
            raise SparseEmbeddingError(
                ErrorCategory.VALIDATION_ERROR,
                f"indices and values row count differ: {idx.shape[0]} != {val.shape[0]}",
                details={"nnz_indices": int(idx.shape[0]), "nnz_values": int(val.shape[0])},
            )
        if val.shape[1] != expected_dim:
            raise SparseEmbeddingError(
                ErrorCategory.VALIDATION_ERROR,
                f"values dim {val.shape[1]} does not match table dim {expected_dim}",
                details={"got_dim": int(val.shape[1]), "expected_dim": int(expected_dim)},
            )

        nnz = int(idx.shape[0])
        # An empty batch performs no division, so its (unused) scale is stored
        # as 0.0 and is not subject to the positive-scale rule.
        if nnz == 0:
            if scale is not None and not np.isfinite(scale):
                raise SparseEmbeddingError(
                    ErrorCategory.VALIDATION_ERROR,
                    f"scale must be finite, got {scale!r}",
                    details={"scale": float(scale)},
                )
            eff_scale = float(scale) if scale is not None else 0.0
        else:
            eff_scale = float(nnz) if scale is None else float(scale)
            if not np.isfinite(eff_scale) or eff_scale <= 0.0:
                raise SparseEmbeddingError(
                    ErrorCategory.VALIDATION_ERROR,
                    f"scale must be finite and positive, got {eff_scale!r}",
                    details={"scale": eff_scale},
                )

        if nnz > 0:
            if not np.all(np.isfinite(val)):
                n_bad = int(np.count_nonzero(~np.isfinite(val)))
                raise SparseEmbeddingError(
                    ErrorCategory.NUMERIC_ERROR,
                    f"values contain {n_bad} non-finite entries (NaN/Inf)",
                    details={"non_finite_entries": n_bad},
                )
            # Casting a non-integral float index would silently truncate.
            if not np.all(idx.astype(np.float64) == idx):
                raise SparseEmbeddingError(
                    ErrorCategory.VALIDATION_ERROR,
                    "indices must be integral",
                )
            out = np.nonzero((idx < 0) | (idx >= vocab_size))[0]
            if out.size > 0:
                first = int(out[0])
                raise SparseEmbeddingError(
                    ErrorCategory.INDEX_OUT_OF_RANGE,
                    f"index {int(idx[first])} at position {first} is outside [0, {vocab_size})",
                    details={
                        "bad_position": first,
                        "bad_index": int(idx[first]),
                        "vocab_size": int(vocab_size),
                        "n_bad": int(out.size),
                    },
                )

        return cls(
            indices=np.array(idx, dtype=np.int64, copy=True),
            values=np.array(val, copy=True, order="C"),
            scale=eff_scale,
        )


def _as_1d_int_array(obj: Any) -> np.ndarray:
    arr = np.asarray(obj)
    if arr.ndim != 1:
        raise SparseEmbeddingError(
            ErrorCategory.VALIDATION_ERROR,
            f"indices must be 1-D, got shape {arr.shape}",
            details={"shape": list(arr.shape)},
        )
    if not np.issubdtype(arr.dtype, np.integer):
        # Allow float-encoded ints only when every value is integral; an
        # empty array is accepted (empty batches are legal input).
        if np.issubdtype(arr.dtype, np.floating) and (
            arr.size == 0 or bool(np.all(np.equal(np.mod(arr, 1), 0)))
        ):
            arr = arr.astype(np.int64)
        else:
            raise SparseEmbeddingError(
                ErrorCategory.VALIDATION_ERROR,
                f"indices must be integer typed, got dtype {arr.dtype}",
                details={"dtype": str(arr.dtype)},
            )
    return np.ascontiguousarray(arr, dtype=np.int64)


def _as_2d_float_array(obj: Any, *, expected_dim: int) -> np.ndarray:
    arr = np.asarray(obj, dtype=np.float64)
    # An empty JSON list serializes to shape (0,); canonicalize to (0, dim).
    if arr.ndim == 1 and arr.shape[0] == 0:
        arr = arr.reshape(0, expected_dim)
    if arr.ndim != 2:
        raise SparseEmbeddingError(
            ErrorCategory.VALIDATION_ERROR,
            f"values must be 2-D (nnz, dim), got shape {arr.shape}",
            details={"shape": list(arr.shape)},
        )
    return np.ascontiguousarray(arr, dtype=np.float64)
