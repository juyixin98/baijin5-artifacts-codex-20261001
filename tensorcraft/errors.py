"""Typed error hierarchy.

Every failure raised by the core belongs to a stable ``category`` string so
that API responses and tests can assert the *failure class* rather than
matching prose. Categories:

    SHAPE_MISMATCH         ranks / dimensions incompatible
    SIZE_MISMATCH          element count does not match (e.g. reshape)
    INDEX_OUT_OF_BOUNDS    an integer index lies outside its axis
    INVALID_INDEX          structurally invalid index (e.g. step == 0)
    OVERFLOW               integer product/offset would overflow
    OVERLAPPING_WRITE      write targets a self-overlapping view
    NON_CONTIGUOUS_VIEW    a zero-copy layout request cannot be honoured
    INVALID_STRIDE         stride/offset combination invalid for a buffer
    BROADCAST_ERROR        operands cannot be broadcast together
    DTYPE_MISMATCH         operands have incompatible dtypes
    NUMERIC_ERROR          runtime numerical failure (e.g. integer /0)
    UNSUPPORTED_DTYPE      dtype outside the supported numeric set
    STATE_ERROR            unknown handle / invalid registry transition
    CONFIG_ERROR           invalid configuration value
    GRAPH_ERROR        malformed computation graph (cycle, unknown node)
    STORAGE_LIMIT_EXCEEDED  requested element count above the configured cap
"""

from __future__ import annotations


class TensorCraftError(Exception):
    """Base class for all core errors."""

    category = "CORE_ERROR"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict = dict(details or {})

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "message": self.message,
            "details": self.details,
        }


class ShapeMismatchError(TensorCraftError):
    category = "SHAPE_MISMATCH"


class SizeMismatchError(TensorCraftError):
    category = "SIZE_MISMATCH"


class IndexOutOfBoundsError(TensorCraftError):
    category = "INDEX_OUT_OF_BOUNDS"


class InvalidIndexError(TensorCraftError):
    category = "INVALID_INDEX"


class OverflowErrorCore(TensorCraftError):
    category = "OVERFLOW"


class OverlapWriteError(TensorCraftError):
    category = "OVERLAPPING_WRITE"


class NonContiguousViewError(TensorCraftError):
    category = "NON_CONTIGUOUS_VIEW"


class InvalidStrideError(TensorCraftError):
    category = "INVALID_STRIDE"


class BroadcastError(TensorCraftError):
    category = "BROADCAST_ERROR"


class DTypeMismatchError(TensorCraftError):
    category = "DTYPE_MISMATCH"


class NumericError(TensorCraftError):
    category = "NUMERIC_ERROR"


class UnsupportedDTypeError(TensorCraftError):
    category = "UNSUPPORTED_DTYPE"


class StateError(TensorCraftError):
    category = "STATE_ERROR"


class ConfigError(TensorCraftError):
    category = "CONFIG_ERROR"


class GraphError(TensorCraftError):
    category = "GRAPH_ERROR"


class StorageLimitExceededError(TensorCraftError):
    category = "STORAGE_LIMIT_EXCEEDED"


# Registry for mapping a category string back to its exception type
# (used by API-layer serialization).
CATEGORY_TO_ERROR: dict[str, type[TensorCraftError]] = {
    cls.category: cls
    for cls in (
        ShapeMismatchError,
        SizeMismatchError,
        IndexOutOfBoundsError,
        InvalidIndexError,
        OverflowErrorCore,
        OverlapWriteError,
        NonContiguousViewError,
        InvalidStrideError,
        BroadcastError,
        DTypeMismatchError,
        NumericError,
        UnsupportedDTypeError,
        StateError,
        ConfigError,
        GraphError,
        StorageLimitExceededError,
    )
}
