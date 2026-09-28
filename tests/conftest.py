"""Shared pytest fixtures and helpers."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from sparse_embeddings.config import (
    ClippingConfig,
    OptimizerConfig,
    ServiceConfig,
    TableConfig,
)
from sparse_embeddings.observability import NullLogger
from sparse_embeddings.state import SparseOptimizerService
from sparse_embeddings.validation import DenseReferenceModel

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def fixture_payload() -> dict:
    with (FIXTURE_DIR / "small_batches.json").open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def fixture_batches(fixture_payload) -> dict[str, dict]:
    return {b["name"]: b for b in fixture_payload["batches"]}


def make_config(
    *,
    name: str = "embed",
    vocab_size: int = 8,
    dim: int = 4,
    optimizer: str = "momentum_sgd",
    lr: float = 0.01,
    momentum: float = 0.9,
    clip_mode: str | None = "global",
    max_norm: float = 2.0,
    dtype: str = "float64",
    seed: int = 7,
) -> TableConfig:
    clip = (
        ClippingConfig(mode=clip_mode, max_norm=max_norm)
        if clip_mode is not None
        else None
    )
    return TableConfig(
        name=name,
        vocab_size=vocab_size,
        dim=dim,
        optimizer=OptimizerConfig(name=optimizer, learning_rate=lr, momentum=momentum),
        clipping=clip,
        dtype=dtype,
        seed=seed,
    )


@pytest.fixture
def service_factory(tmp_path):
    """Factory building a service with temp storage and quiet logging."""

    def _factory(*, persist: bool = False, **config_kwargs):
        cfg = make_config(**config_kwargs)
        if persist:
            svc_cfg = ServiceConfig(
                data_dir=tmp_path / "data",
                log_path=tmp_path / "logs" / "svc.jsonl",
                log_to_stderr=False,
            )
            from sparse_embeddings.persistence import CheckpointStore

            store = CheckpointStore(svc_cfg.data_dir / "checkpoints")
        else:
            svc_cfg = None
            store = None
        svc = SparseOptimizerService(logger=NullLogger(), store=store)
        table = svc.create_table(cfg)
        return svc, table, cfg

    return _factory


@pytest.fixture
def paired_factory(service_factory):
    """Sparse service + independent dense reference sharing initial weights."""

    def _factory(**config_kwargs):
        svc, table, cfg = service_factory(**config_kwargs)
        ref = DenseReferenceModel.from_config(cfg, initial_weights=table.weights)
        return svc, table, ref, cfg

    return _factory


def state_fingerprint(table) -> tuple:
    """Hashable fingerprint of ALL state including untouched rows."""
    return (
        table.weights.tobytes(),
        table.momentum.tobytes(),
        table.row_steps.tobytes(),
        table.global_step,
        table.ever_touched.tobytes(),
    )
