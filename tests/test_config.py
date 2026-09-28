"""Configuration-layer fail-fast validation."""

from __future__ import annotations

import pytest

from sparse_embeddings.config import ClippingConfig, OptimizerConfig, TableConfig
from sparse_embeddings.tensor_types import ErrorCategory, SparseEmbeddingError


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "per-token", "max_norm": 1.0},
        {"mode": "GLOBAL", "max_norm": 1.0},
        {"mode": "global", "max_norm": 0.0},
        {"mode": "global", "max_norm": -1.0},
        {"mode": "row", "max_norm": float("nan")},
    ],
)
def test_invalid_clipping_config_raises_config_error(kwargs):
    with pytest.raises(SparseEmbeddingError) as exc:
        ClippingConfig(**kwargs)
    assert exc.value.category is ErrorCategory.CONFIG_ERROR


def test_global_and_row_are_the_only_clip_modes():
    assert ClippingConfig("global", 1.0).mode == "global"
    assert ClippingConfig("row", 1.0).mode == "row"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "adam"},  # unsupported optimizer
        {"learning_rate": 0.0},
        {"learning_rate": -0.1},
        {"momentum": 1.0},
        {"momentum": -0.1},
    ],
)
def test_invalid_optimizer_config(kwargs):
    with pytest.raises(SparseEmbeddingError) as exc:
        OptimizerConfig(**kwargs)
    assert exc.value.category is ErrorCategory.CONFIG_ERROR


def test_sgd_does_not_require_momentum_bounds():
    assert OptimizerConfig(name="sgd").momentum == 0.9  # stored but unused


def test_table_config_dim_and_vocab_must_be_positive():
    with pytest.raises(SparseEmbeddingError) as exc:
        TableConfig(name="t", vocab_size=0, dim=4, optimizer=OptimizerConfig())
    assert exc.value.category is ErrorCategory.CONFIG_ERROR
    with pytest.raises(SparseEmbeddingError) as exc:
        TableConfig(name="t", vocab_size=8, dim=0, optimizer=OptimizerConfig())
    assert exc.value.category is ErrorCategory.CONFIG_ERROR


def test_unknown_dtype_rejected():
    with pytest.raises(SparseEmbeddingError):
        TableConfig(
            name="t",
            vocab_size=8,
            dim=4,
            optimizer=OptimizerConfig(),
            dtype="bfloat16",
        )
