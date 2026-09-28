"""Persistence tests: touched-only writes, transaction boundary, restore."""

from __future__ import annotations

import numpy as np
import pytest

from sparse_embeddings.config import ClippingConfig, OptimizerConfig, TableConfig
from sparse_embeddings.observability import NullLogger
from sparse_embeddings.persistence import CheckpointStore, restore_into
from sparse_embeddings.state import SparseOptimizerService
from sparse_embeddings.tensor_types import (
    ErrorCategory,
    SparseEmbeddingError,
    SparseGradientBatch,
)
from sparse_embeddings.validation import DenseReferenceModel, assert_sparse_matches_dense
from tests.conftest import make_config, state_fingerprint


def _batch(indices, values, scale=None):
    return SparseGradientBatch.from_lists(
        indices, values, vocab_size=8, expected_dim=4, scale=scale
    )


@pytest.fixture
def persisted_service(tmp_path):
    store = CheckpointStore(tmp_path / "ckpt")
    svc = SparseOptimizerService(logger=NullLogger(), store=store)
    cfg = make_config(clip_mode="global", max_norm=5.0)
    table = svc.create_table(cfg)
    return svc, table, cfg, store


def test_checkpoint_contains_only_touched_rows(persisted_service):
    svc, table, cfg, store = persisted_service
    svc.apply_batch(cfg.name, _batch([0, 7, 0], np.ones((3, 4))), run_id="r1")
    data = np.load(store.path_for(cfg.name), allow_pickle=True)
    np.testing.assert_array_equal(np.sort(data["touched_indices"]), [0, 7])
    assert data["weight_rows"].shape == (2, 4)
    assert data["momentum_rows"].shape == (2, 4)
    np.testing.assert_array_equal(data["row_steps"], [1, 1])


def test_checkpoint_grows_only_with_new_touched_rows(persisted_service):
    svc, table, cfg, store = persisted_service
    svc.apply_batch(cfg.name, _batch([3], np.ones((1, 4))), run_id="r1")
    size1 = store.path_for(cfg.name).stat().st_size
    svc.apply_batch(cfg.name, _batch([3], np.ones((1, 4))), run_id="r2")
    size2 = store.path_for(cfg.name).stat().st_size
    # Second batch touches no new row: checkpoint should not scale with V.
    assert size2 <= size1 + 512  # tiny metadata delta tolerance only


def test_restore_reproduces_state_and_leaves_untouched_seeded(persisted_service, tmp_path):
    svc, table, cfg, store = persisted_service
    svc.apply_batch(cfg.name, _batch([0, 7, 0], np.ones((3, 4))), run_id="r1")
    svc.apply_batch(cfg.name, _batch([7, 2], np.full((2, 4), 0.3)), run_id="r2")
    expected_weights = table.weights.copy()
    expected_momentum = table.momentum.copy()

    svc2 = SparseOptimizerService(logger=NullLogger())
    restored = restore_into(svc2, cfg.name, store)
    np.testing.assert_array_equal(restored.weights, expected_weights)
    np.testing.assert_array_equal(restored.momentum, expected_momentum)
    np.testing.assert_array_equal(restored.row_steps, table.row_steps)
    assert restored.global_step == 2
    # Restored table continues training identically to a dense reference.
    ref = DenseReferenceModel.from_config(cfg, initial_weights=restored.weights)
    # Replay after restore: reference starts from same weights/momentum.
    ref.momentum = restored.momentum.copy()
    ref.row_steps = restored.row_steps.copy()
    ref.global_step = restored.global_step
    b = _batch([1, 2], np.full((2, 4), 0.2))
    ref.apply(b.indices, b.values, b.scale)
    svc2.apply_batch(cfg.name, b, run_id="post-restore")
    assert_sparse_matches_dense(restored, ref)


def test_persistence_failure_aborts_step_and_keeps_old_checkpoint(
    persisted_service, monkeypatch
):
    svc, table, cfg, store = persisted_service
    svc.apply_batch(cfg.name, _batch([0], np.ones((1, 4))), run_id="good-1")
    old_bytes = store.path_for(cfg.name).read_bytes()
    fp_before = state_fingerprint(table)
    step_before = table.global_step

    def boom(*_args, **_kwargs):
        raise OSError("simulated disk fault")

    monkeypatch.setattr("sparse_embeddings.persistence.os.replace", boom)
    with pytest.raises(SparseEmbeddingError) as exc:
        svc.apply_batch(cfg.name, _batch([1], np.ones((1, 4))), run_id="fail-1")
    assert exc.value.category is ErrorCategory.PERSISTENCE_ERROR

    # In-memory state did NOT swap...
    assert table.global_step == step_before
    assert state_fingerprint(table) == fp_before
    # ...and the previous checkpoint is byte-identical and still loadable.
    assert store.path_for(cfg.name).read_bytes() == old_bytes
    # No temp file left behind.
    leftovers = list(store.directory.glob("*.tmp"))
    assert leftovers == []


def test_no_partial_checkpoint_files_after_successful_commit(persisted_service):
    svc, table, cfg, store = persisted_service
    svc.apply_batch(cfg.name, _batch([5], np.ones((1, 4))), run_id="r")
    assert list(store.directory.glob(".*.tmp")) == []


def test_restore_refuses_to_overwrite_live_table(persisted_service):
    svc, table, cfg, store = persisted_service
    svc.apply_batch(cfg.name, _batch([0], np.ones((1, 4))), run_id="r")
    with pytest.raises(SparseEmbeddingError) as exc:
        restore_into(svc, cfg.name, store)
    assert exc.value.category is ErrorCategory.STATE_CONFLICT


def test_load_missing_checkpoint_is_not_found(tmp_path):
    store = CheckpointStore(tmp_path / "ckpt")
    with pytest.raises(SparseEmbeddingError) as exc:
        store.load("ghost")
    assert exc.value.category is ErrorCategory.NOT_FOUND
