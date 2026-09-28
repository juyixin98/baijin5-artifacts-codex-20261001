"""Negative-path tests for the equivalence harness itself."""

from __future__ import annotations

import numpy as np
import pytest

from sparse_embeddings.state import StepResult
from sparse_embeddings.tensor_types import SparseGradientBatch
from sparse_embeddings.validation import compare_states
from sparse_embeddings.validation.equivalence import assert_step_matches_reference


def test_mismatch_report_names_fields_and_rows(paired_factory):
    svc, table, ref, cfg = paired_factory(clip_mode=None)
    batch = SparseGradientBatch.from_lists(
        [1, 1, 3], np.ones((3, 4)), vocab_size=8, expected_dim=4
    )
    svc.apply_batch(cfg.name, batch, run_id="x")

    # Tamper with the independent reference so several fields diverge.
    ref.weights[1] += 5.0
    ref.row_steps[1] += 1
    ref.global_step = 7

    report = compare_states(table, ref)
    assert report.matched is False
    fields = {f["field"] for f in report.failures}
    assert {"weights", "row_steps", "global_step"} <= fields
    weight_failure = next(f for f in report.failures if f["field"] == "weights")
    assert "1" in weight_failure["message"]  # offending row id surfaced

    with pytest.raises(AssertionError, match="sparse/dense mismatch"):
        report.raise_if_failed()

    assert report.summary().startswith("MISMATCH")


def test_matching_report_summarizes_ok(paired_factory):
    svc, table, ref, cfg = paired_factory()
    batch = SparseGradientBatch.from_lists(
        [0], np.ones((1, 4)), vocab_size=8, expected_dim=4
    )
    ref.apply(batch.indices, batch.values, batch.scale)
    svc.apply_batch(cfg.name, batch, run_id="ok")
    report = compare_states(table, ref)
    assert report.matched is True
    assert "MATCH" in report.summary()
    report.raise_if_failed()  # must not raise


def test_shape_mismatch_is_reported(paired_factory):
    svc, table, ref, cfg = paired_factory()
    ref.weights = np.zeros((cfg.vocab_size, cfg.dim + 1))
    report = compare_states(table, ref)
    assert report.matched is False
    assert any(
        f["field"] == "weights" and "shape mismatch" in f["message"]
        for f in report.failures
    )


def _minimal_step_result(**over) -> StepResult:
    base = dict(
        run_id="r",
        table="t",
        stepped=True,
        reason="applied",
        global_step_before=0,
        global_step_after=1,
        nnz=1,
        n_unique_touched=1,
        touched_indices=np.array([0]),
        token_counts=np.array([1]),
        zero_gradient_rows=np.empty(0, dtype=np.int64),
        clip_mode=None,
        pre_clip_global_norm=0.0,
        post_clip_global_norm=0.0,
        clip_applied=False,
        per_row_scales=np.ones(1),
    )
    base.update(over)
    return StepResult(**base)


def test_step_compare_flags_different_stepped_flag():
    result = _minimal_step_result()
    with pytest.raises(AssertionError, match="stepped flag"):
        assert_step_matches_reference(result, {"stepped": False})


def test_step_compare_empty_steps_agree():
    result = _minimal_step_result(
        stepped=False,
        reason="empty_batch_no_step",
        global_step_after=0,
        n_unique_touched=0,
        touched_indices=np.empty(0, dtype=np.int64),
        token_counts=np.empty(0, dtype=np.int64),
        per_row_scales=np.empty(0),
    )
    # No further assertions when neither side stepped: returns silently.
    assert_step_matches_reference(result, {"stepped": False})


def test_step_compare_detects_norm_and_scale_disagreement():
    result = _minimal_step_result(
        pre_clip_global_norm=1.0,
        post_clip_global_norm=0.5,
        clip_applied=True,
        per_row_scales=np.array([0.5]),
    )
    ref_outcome = {
        "stepped": True,
        "touched": np.array([0]),
        "pre_norm": 2.0,
        "post_norm": 1.0,
        "clipped": True,
        "scales": np.array([0.25]),
    }
    with pytest.raises(AssertionError, match="pre-clip global norms differ"):
        assert_step_matches_reference(result, ref_outcome)
