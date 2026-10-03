"""Service-level contract: explicit failure categories and diagnostics."""

import numpy as np
import pytest

from dtw_service.contracts import (
    AlignmentRequest,
    DecisionStatus,
    FailureCategory,
)
from dtw_service.service import align
from dtw_service.settings import Settings

SETTINGS = Settings(sakoe_chiba_radius=4, smoothing_window=3)


def _request(a, b, radius=None, record_id="rec-1"):
    return AlignmentRequest(sequence_a=a, sequence_b=b,
                            window_radius=radius, record_id=record_id)


def test_empty_sequence_rejected_with_category():
    res = align(_request([], [1.0, 2.0]), SETTINGS)
    assert res.status is DecisionStatus.REJECTED
    assert res.failure.category is FailureCategory.EMPTY_SEQUENCE
    assert res.path is None and res.cost is None
    assert res.diagnostics["request_id"] == "rec-1"


def test_non_finite_values_rejected():
    res = align(_request([0.0, float("nan"), 1.0], [0.0, 1.0, 2.0]), SETTINGS)
    assert res.status is DecisionStatus.REJECTED
    assert res.failure.category is FailureCategory.NON_FINITE_VALUES


def test_length_limit_rejected():
    settings = Settings(sakoe_chiba_radius=4, smoothing_window=3,
                        max_sequence_length=5)
    res = align(_request([0.0] * 6, [0.0] * 6), settings)
    assert res.failure.category is FailureCategory.LENGTH_LIMIT_EXCEEDED


def test_negative_window_rejected():
    res = align(_request([1.0], [1.0], radius=-1), SETTINGS)
    assert res.failure.category is FailureCategory.INVALID_WINDOW


def test_too_narrow_window_rejected_before_dp():
    # |n - m| = 6 > radius 2: endpoint can never be inside the band.
    res = align(_request([0.0] * 10, [0.0] * 4, radius=2), SETTINGS)
    assert res.status is DecisionStatus.REJECTED
    assert res.failure.category is FailureCategory.WINDOW_TOO_NARROW
    assert "exceeds window radius" in res.failure.message


def test_accepted_alignment_carries_path_cost_stretch():
    res = align(_request([0.0, 1.0], [0.0, 2.0], radius=1), SETTINGS)
    assert res.status is DecisionStatus.ACCEPTED
    assert res.path == [[0, 0], [1, 1]]
    assert res.cost == pytest.approx(1.0)
    # Fixed denominator convention: n + m = 4.
    assert res.normalized_cost == pytest.approx(0.25)
    assert res.stretch == [1.0]
    assert res.diagnostics["normalization_denominator"] == 4


def test_identical_constant_sequences_are_indeterminate():
    # Every band cell has distance zero: all legal paths equally optimal.
    res = align(_request([1.0, 1.0, 1.0], [1.0, 1.0, 1.0], radius=1), SETTINGS)
    assert res.status is DecisionStatus.INDETERMINATE
    assert res.cost == pytest.approx(0.0)
    assert res.path is not None
    assert "not identifiable" in res.diagnostics["reason"]


def test_diagnostics_mask_sequence_payloads():
    secret = [12345.6789, 98765.4321]
    res = align(_request(secret, secret, radius=0), SETTINGS)
    blob = str(res.diagnostics)
    assert "12345.6789" not in blob and "98765.4321" not in blob
    assert "sha256=" in blob and "len=2" in blob


def test_request_id_generated_when_absent():
    req = AlignmentRequest(sequence_a=[0.0], sequence_b=[0.0], window_radius=0)
    res = align(req, SETTINGS)
    assert res.request_id and res.request_id == res.diagnostics["request_id"]
