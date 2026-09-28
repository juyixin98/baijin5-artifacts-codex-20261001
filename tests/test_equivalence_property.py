"""Property-style equivalence: randomized streams vs the independent dense ref.

These are the reviewer-facing cross-checks: random batches with duplicate
ids, hot/cold rows, zero rows, empty batches and large gradients, across
optimizers, both clip modes, both storage dtypes. The oracle is exclusively
``validation.dense_reference``.
"""

from __future__ import annotations

import numpy as np
import pytest

from sparse_embeddings.tensor_types import SparseGradientBatch
from sparse_embeddings.validation import (
    DenseReferenceModel,
    assert_sparse_matches_dense,
    assert_step_matches_reference,
)
from tests.conftest import make_config


def _random_stream(rng, vocab_size: int, dim: int, n_batches: int):
    for _ in range(n_batches):
        kind = rng.choice(
            ["normal", "duplicates", "hot", "zero_row", "empty", "huge"],
            p=[0.3, 0.2, 0.2, 0.1, 0.1, 0.1],
        )
        if kind == "empty":
            yield np.empty(0, dtype=np.int64), np.empty((0, dim))
            continue
        n = int(rng.integers(1, 12))
        if kind == "hot":
            hot_ids = rng.integers(0, vocab_size, size=2)
            idx = rng.choice(hot_ids, size=n)
        else:
            idx = rng.integers(0, vocab_size, size=n)
        if kind == "duplicates":
            idx = rng.choice(rng.integers(0, vocab_size, size=3), size=n)
        if kind == "huge":
            val = rng.normal(0.0, 50.0, size=(n, dim))
        else:
            val = rng.normal(0.0, 1.0, size=(n, dim))
        if kind == "zero_row" and n >= 2:
            # Force exact cancellation on the first selected row.
            idx[0] = idx[1]
            val[1] = -val[0]
        yield idx.astype(np.int64), val


@pytest.mark.parametrize("optimizer", ["sgd", "momentum_sgd"])
@pytest.mark.parametrize("clip_mode", ["global", "row", None])
@pytest.mark.parametrize("dtype", ["float64", "float32"])
def test_random_streams_match_dense_reference(optimizer, clip_mode, dtype):
    rng = np.random.default_rng(12345)
    vocab_size, dim = 13, 5
    cfg = make_config(
        name=f"p-{optimizer}-{clip_mode}-{dtype}",
        vocab_size=vocab_size,
        dim=dim,
        optimizer=optimizer,
        lr=0.02,
        momentum=0.8,
        clip_mode=clip_mode,
        max_norm=1.5,
        dtype=dtype,
        seed=99,
    )
    from sparse_embeddings.observability import NullLogger
    from sparse_embeddings.state import SparseOptimizerService

    svc = SparseOptimizerService(logger=NullLogger())
    table = svc.create_table(cfg)
    ref = DenseReferenceModel.from_config(cfg, initial_weights=table.weights)

    stream = list(_random_stream(rng, vocab_size, dim, n_batches=40))
    stepped = 0
    for k, (idx, val) in enumerate(stream):
        batch = SparseGradientBatch.from_lists(
            idx, val, vocab_size=vocab_size, expected_dim=dim
        )
        ref_out = ref.apply(
            batch.indices, batch.values, batch.scale if batch.nnz else 1.0
        )
        result = svc.apply_batch(cfg.name, batch, run_id=f"prop-{k}")
        assert_step_matches_reference(
            result,
            ref_out,
            rtol=1e-6 if dtype == "float32" else 1e-10,
            atol=1e-6 if dtype == "float32" else 1e-10,
        )
        stepped += int(result.stepped)
    assert stepped > 0  # stream must have done real work
    # Final whole-state equivalence, untouched rows included.
    assert_sparse_matches_dense(
        table,
        ref,
        rtol=1e-6 if dtype == "float32" else 1e-10,
        atol=1e-6 if dtype == "float32" else 1e-10,
    )
    # Every row the reference never touched must be exactly seeded and frozen.
    rng_init = np.random.default_rng(cfg.seed)
    seeded = rng_init.normal(0.0, 0.1, (vocab_size, dim)).astype(cfg.numpy_dtype)
    cold = np.nonzero(ref.row_steps == 0)[0]
    np.testing.assert_array_equal(table.row_steps[cold], 0)
    np.testing.assert_array_equal(table.weights[cold], seeded[cold])
    np.testing.assert_array_equal(table.momentum[cold], 0.0)


@pytest.mark.slow
def test_large_vocab_touches_small_fraction():
    """Big V: work/checkpoint scale with touched rows, not vocabulary size."""
    rng = np.random.default_rng(2026)
    vocab_size, dim = 200_000, 8
    cfg = make_config(
        name="big", vocab_size=vocab_size, dim=dim, clip_mode="global", max_norm=3.0
    )
    from sparse_embeddings.observability import NullLogger
    from sparse_embeddings.state import SparseOptimizerService

    svc = SparseOptimizerService(logger=NullLogger())
    table = svc.create_table(cfg)
    idx = rng.integers(0, vocab_size, size=500)
    val = rng.normal(size=(500, dim))
    batch = SparseGradientBatch.from_lists(idx, val, vocab_size=vocab_size, expected_dim=dim)
    result = svc.apply_batch("big", batch, run_id="big-1")
    assert result.global_step_after == 1
    assert result.n_unique_touched <= 500
    # The overwhelming majority of rows are untouched and still seeded.
    assert np.count_nonzero(table.ever_touched) < vocab_size * 0.01
    assert table.momentum[vocab_size - 1].sum() == 0.0
