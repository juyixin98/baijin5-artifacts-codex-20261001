"""HTTP request/response schemas (pydantic v2).

Wire format uses plain JSON arrays; conversion to validated
:class:`SparseGradientBatch` happens in the route so index-range failures are
raised *before* any state change and reported with their typed category.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from sparse_embeddings.config import ClippingConfig, OptimizerConfig, TableConfig
from sparse_embeddings.tensor_types import SparseGradientBatch


class OptimizerSpec(BaseModel):
    name: str = "momentum_sgd"
    learning_rate: float = 0.01
    momentum: float = 0.9


class ClippingSpec(BaseModel):
    mode: str = Field(description="'global' or 'row'; fixed for the table's lifetime")
    max_norm: float


class CreateTableRequest(BaseModel):
    name: str
    vocab_size: int = Field(gt=0)
    dim: int = Field(gt=0)
    optimizer: OptimizerSpec = OptimizerSpec()
    clipping: ClippingSpec | None = None
    dtype: str = "float32"
    seed: int = 0

    def to_config(self) -> TableConfig:
        clip = (
            ClippingConfig(mode=self.clipping.mode, max_norm=self.clipping.max_norm)
            if self.clipping is not None
            else None
        )
        return TableConfig(
            name=self.name,
            vocab_size=self.vocab_size,
            dim=self.dim,
            optimizer=OptimizerConfig(
                name=self.optimizer.name,
                learning_rate=self.optimizer.learning_rate,
                momentum=self.optimizer.momentum,
            ),
            clipping=clip,
            dtype=self.dtype,
            seed=self.seed,
        )


class ApplyBatchRequest(BaseModel):
    indices: list[int]
    values: list[list[float]]
    scale: float | None = Field(
        default=None,
        description="reduction divisor; defaults to token count (mean over tokens)",
    )
    run_id: str | None = None

    def to_batch(self, *, vocab_size: int, dim: int) -> SparseGradientBatch:
        return SparseGradientBatch.from_lists(
            self.indices,
            self.values,
            vocab_size=vocab_size,
            expected_dim=dim,
            scale=self.scale,
        )


class RowsRequest(BaseModel):
    indices: list[int]


class ErrorResponse(BaseModel):
    ok: bool = False
    error: dict[str, Any]
    run_id: str | None = None


class StepResponse(BaseModel):
    ok: bool = True
    result: dict[str, Any]
    run_id: str
