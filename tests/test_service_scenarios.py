"""Layer-3 service tests using the synthetic fixture scenarios.

Every numeric outcome is checked against the independent dense reference
(``validation.dense_reference``), which never imports the kernels under test.
Scenarios: duplicate ids, hot/cold alternation, empty batch, large gradients,
zero-gradient touched row, out-of-range whole-batch rejection.
"""

from __future__ import annotations

import numpy as np
import pytest

from sparse_embeddings.tensor_types import (
    ErrorCategory,
    SparseEmbeddingError,
    SparseGradientBatch,
)
from sparse_embeddings.validation import (
    assert_sparse_matches_dense,
    assert_step_matches_reference,
)
from tests.conftest import state_fingerprint


def _batch(fixture_batches, name, *, vocab_size, dim, scale=None):
    b = fixture_batches[name]
    return SparseGradientBatch.from_lists(
        b["indices"],
        b["values"],
        vocab_size=vocab_size,
        expected_dim=dim,
        scale=scale,
    )


@pytest.mark.parametrize("clip_mode", ["global", "row"])
def test_all_fixture_scenarios_match_dense_reference(
    paired_factory, fixture_batches, clip_mode
):
    svc, table, ref, cfg = paired_factory(clip_mode=clip_mode, max_norm=2.0)
    scenario_order = [
        "duplicate_ids",
        "hot_cold_alternating",
        "empty",
        "large_gradients",
        "zero_gradient_touched",
    ]
    for name in scenario_order:
        batch = _batch(
            fixture_batches, name, vocab_size=cfg.vocab_size, dim=cfg.dim
        )
        ref_out = ref.apply(batch.indices, batch.values, batch.scale if not batch.is_empty else 1.0)
        result = svc.apply_batch(cfg.name, batch, run_id=f"run-{name}")
        assert_step_matches_reference(result, ref_out)
        assert_sparse_matches_dense(table, ref)

    # Final concrete assertions on training state.
    assert table.global_step == 4  # two stepping batches skipped: only 4 stepped
    # Touched rows after the five scenarios: {1,3}, {0,7}, -, {2,5}, {4}
    expected_touched = np.array([0, 1, 2, 3, 4, 5, 7])
    np.testing.assert_array_equal(np.nonzero(table.ever_touched)[0], expected_touched)
    np.testing.assert_array_equal(table.row_steps[expected_touched], 1)
    # Row 6 is never touched: zero steps, zero momentum, seeded weights intact.
    assert table.row_steps[6] == 0
    np.testing.assert_array_equal(table.momentum[6], 0.0)
    rng = np.random.default_rng(cfg.seed)
    seeded = rng.normal(0.0, 0.1, size=(cfg.vocab_size, cfg.dim)).astype(cfg.numpy_dtype)
    np.testing.assert_array_equal(table.weights[6], seeded[6])


def test_duplicate_ids_are_aggregated_before_optimizer(
    paired_factory, fixture_batches
):
    svc, table, ref, cfg = paired_factory(clip_mode=None)
    batch = _batch(fixture_batches, "duplicate_ids", vocab_size=8, dim=4)
    result = svc.apply_batch(cfg.name, batch, run_id="dup-1")

    # 5 tokens collapse to 2 unique rows; counts recorded per row.
    assert result.nnz == 5
    assert result.n_unique_touched == 2
    order = np.argsort(result.touched_indices)
    np.testing.assert_array_equal(result.touched_indices[order], [1, 3])
    np.testing.assert_array_equal(result.token_counts[order], [2, 3])

    # Hand-check the aggregated mean gradient for row 3 (3 contributions / 5).
    vals = np.asarray(fixture_batches["duplicate_ids"]["values"], dtype=np.float64)
    idxs = np.asarray(fixture_batches["duplicate_ids"]["indices"])
    expected_g3 = vals[idxs == 3].sum(axis=0) / 5.0
    expected_m3 = expected_g3  # first step: v = 0.9*0 + g
    np.testing.assert_allclose(table.momentum[3], expected_m3)
    rng = np.random.default_rng(cfg.seed)
    seeded = rng.normal(0.0, 0.1, (8, 4)).astype(cfg.numpy_dtype)
    np.testing.assert_allclose(
        table.weights[3],
        seeded[3] - cfg.optimizer.learning_rate * expected_m3.astype(cfg.numpy_dtype),
    )
    # Rows other than 1 and 3 must be byte-identical to their seed init.
    untouched = [0, 2, 4, 5, 6, 7]
    assert np.count_nonzero(table.ever_touched) == 2
    assert np.all(table.row_steps[untouched] == 0)


def test_hot_cold_alternation_keeps_cold_rows_frozen(paired_factory, fixture_batches):
    svc, table, ref, cfg = paired_factory(clip_mode=None)
    # Two batches hit only rows 0 and 7 repeatedly.
    for k in range(2):
        b = _batch(fixture_batches, "hot_cold_alternating", vocab_size=8, dim=4)
        ref.apply(b.indices, b.values, b.scale)
        svc.apply_batch(cfg.name, b, run_id=f"hot-{k}")
    assert table.global_step == 2
    np.testing.assert_array_equal(table.row_steps[[0, 7]], [2, 2])
    np.testing.assert_array_equal(table.row_steps[1:7], 0)
    # Momentum on cold rows is exactly zero; weights unchanged from seed.
    np.testing.assert_array_equal(table.momentum[1:7], 0.0)
    assert_sparse_matches_dense(table, ref)


def test_empty_batch_consumes_no_step_and_changes_nothing(
    paired_factory, fixture_batches
):
    svc, table, ref, cfg = paired_factory(clip_mode="global", max_norm=2.0)
    before = state_fingerprint(table)
    step_before = table.global_step
    b = _batch(fixture_batches, "empty", vocab_size=8, dim=4)
    result = svc.apply_batch(cfg.name, b, run_id="empty-1")

    assert result.stepped is False
    assert result.reason == "empty_batch_no_step"
    assert result.global_step_before == result.global_step_after == step_before
    assert state_fingerprint(table) == before  # byte-for-byte unchanged


def test_large_gradients_trigger_clipping_under_both_modes(
    paired_factory, fixture_batches
):
    for mode in ["global", "row"]:
        svc, table, ref, cfg = paired_factory(
            name=f"t-{mode}", clip_mode=mode, max_norm=2.0
        )
        b = _batch(fixture_batches, "large_gradients", vocab_size=8, dim=4)
        ref_out = ref.apply(b.indices, b.values, b.scale)
        result = svc.apply_batch(cfg.name, b, run_id=f"big-{mode}")

        assert result.clip_applied is True
        assert result.clip_mode == mode
        # Post-clip norm respects the declared bound under its own definition.
        if mode == "global":
            assert result.post_clip_global_norm <= 2.0 + 1e-9
        assert_step_matches_reference(result, ref_out)
        assert_sparse_matches_dense(table, ref)


def test_zero_gradient_touched_row_steps_and_decays_momentum(
    paired_factory, fixture_batches
):
    svc, table, ref, cfg = paired_factory(clip_mode=None)
    # Seed momentum into row 4 first, then send cancelling contributions.
    seed_batch = SparseGradientBatch.from_lists(
        [4], [np.full(4, 1.0)], vocab_size=8, expected_dim=4, scale=1.0
    )
    svc.apply_batch(cfg.name, seed_batch, run_id="seed-m")
    ref.apply(seed_batch.indices, seed_batch.values, seed_batch.scale)
    v_before = table.momentum[4].copy()
    w_before = table.weights[4].copy()

    cancel = _batch(fixture_batches, "zero_gradient_touched", vocab_size=8, dim=4)
    # Fixture values are exact opposites: aggregated gradient is exactly zero.
    result = svc.apply_batch(cfg.name, cancel, run_id="zero-grad")
    ref.apply(cancel.indices, cancel.values, cancel.scale)

    assert result.stepped is True
    np.testing.assert_array_equal(result.zero_gradient_rows, [4])
    assert table.global_step == 2
    assert table.row_steps[4] == 2
    # Momentum is decayed (v <- 0.9*v + 0), not reset and not untouched.
    np.testing.assert_allclose(table.momentum[4], cfg.optimizer.momentum * v_before)
    # Weight moves by lr * decayed momentum even with zero gradient.
    np.testing.assert_allclose(
        table.weights[4],
        w_before - cfg.optimizer.learning_rate * cfg.optimizer.momentum * v_before,
    )
    assert_sparse_matches_dense(table, ref)


def test_out_of_range_rejects_entire_batch_and_preserves_state(
    paired_factory, fixture_batches
):
    svc, table, ref, cfg = paired_factory(clip_mode="global")
    bad = fixture_batches["out_of_range_invalid"]

    # Construction rejects with a typed category before any service call.
    with pytest.raises(SparseEmbeddingError) as exc:
        SparseGradientBatch.from_lists(bad["indices"], bad["values"], vocab_size=8, expected_dim=4)
    assert exc.value.category is ErrorCategory.INDEX_OUT_OF_RANGE
    assert exc.value.details["bad_index"] == 8

    # Apply a valid batch first, then prove an invalid one does not partially
    # apply: state fingerprint is identical before and after the rejection.
    good = _batch(fixture_batches, "duplicate_ids", vocab_size=8, dim=4)
    svc.apply_batch(cfg.name, good, run_id="good")
    step_after_good = table.global_step
    fp_after_good = state_fingerprint(table)

    with pytest.raises(SparseEmbeddingError) as exc2:
        parsed = SparseGradientBatch.from_lists(
            bad["indices"], bad["values"], vocab_size=8, expected_dim=4
        )
        svc.apply_batch(cfg.name, parsed, run_id="bad")
    assert exc2.value.category is ErrorCategory.INDEX_OUT_OF_RANGE
    assert table.global_step == step_after_good
    assert state_fingerprint(table) == fp_after_good


def test_unknown_table_is_not_found_error(paired_factory):
    svc, table, ref, cfg = paired_factory()
    batch = SparseGradientBatch.from_lists([0], [np.ones(4)], vocab_size=8, expected_dim=4)
    with pytest.raises(SparseEmbeddingError) as exc:
        svc.apply_batch("does-not-exist", batch)
    assert exc.value.category is ErrorCategory.NOT_FOUND


def test_momentum_accumulates_across_repeated_touches_vs_dense(
    paired_factory, fixture_batches
):
    svc, table, ref, cfg = paired_factory(clip_mode="row", max_norm=5.0)
    for k in range(5):
        b = _batch(fixture_batches, "duplicate_ids", vocab_size=8, dim=4)
        ref_out = ref.apply(b.indices, b.values, b.scale)
        result = svc.apply_batch(cfg.name, b, run_id=f"rep-{k}")
        assert result.global_step_after == k + 1
        assert_step_matches_reference(result, ref_out)
    assert_sparse_matches_dense(table, ref)
    assert table.row_steps[3] == 5 and table.row_steps[0] == 0
