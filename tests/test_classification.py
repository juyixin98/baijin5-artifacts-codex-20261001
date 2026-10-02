"""Classification tests: each degenerate fixture must land in its declared
failure / uncertainty category — not merely 'the call succeeded'."""
import numpy as np

from app.config import DEFAULT_CONFIG
from app.kernel import estimate_translation
from app.kernel.types import EstimateStatus, FailureCategory, Uncertainty


def test_constant_image_fails_flat(pair):
    ref, mov, _ = pair("constant_image")
    result = estimate_translation(ref, mov, DEFAULT_CONFIG)
    assert result.status is EstimateStatus.FAILED
    assert result.failure_reason is FailureCategory.FLAT_RESPONSE
    assert result.shift is None


def test_periodic_texture_reports_ambiguous_candidates(pair):
    ref, mov, entry = pair("periodic_texture")
    result = estimate_translation(ref, mov, DEFAULT_CONFIG)
    assert result.status is EstimateStatus.UNCERTAIN
    assert Uncertainty.AMBIGUOUS_PEAKS in result.uncertainties
    assert len(result.candidates) > 1
    # the true shift modulo the 16 px period must be among the candidates
    gt = np.array(entry["ground_truth_shift"])
    period = entry["period_px"]
    for cand in result.candidates:
        s = np.array([cand["shift"]["dy"], cand["shift"]["dx"]])
        delta = np.abs(s - gt) % period
        if np.all(np.minimum(delta, period - delta) < 0.5):
            break
    else:
        raise AssertionError(f"no candidate matches ground truth modulo period: "
                             f"{result.candidates} vs {gt}")


def test_brightness_change_is_ok_and_measured(pair):
    ref, mov, entry = pair("brightness_change")
    result = estimate_translation(ref, mov, DEFAULT_CONFIG)
    # brightness change alone must NOT fail or make the result uncertain
    assert result.status is EstimateStatus.OK
    assert Uncertainty.BRIGHTNESS_CHANGE in result.uncertainties
    gt = np.array(entry["ground_truth_shift"])
    assert np.all(np.abs(np.array(result.shift) - gt) < 0.2)
    # gain/offset are measured, not assumed
    assert abs(result.brightness["gain"] - entry["applied_gain"]) < 0.05
    assert result.brightness["offset_frac_of_range"] > 0.02


def test_low_overlap_is_uncertain_not_failed(pair):
    ref, mov, entry = pair("low_overlap")
    result = estimate_translation(ref, mov, DEFAULT_CONFIG)
    assert result.status is EstimateStatus.UNCERTAIN
    assert Uncertainty.LOW_OVERLAP in result.uncertainties
    assert result.confidence["overlap_fraction"] < DEFAULT_CONFIG.min_overlap
    gt = np.array(entry["ground_truth_shift"])
    assert np.all(np.abs(np.array(result.shift) - gt) < 1.0)


def test_no_common_content_fails_with_distinct_reason(pair):
    ref, mov, _ = pair("no_overlap")
    result = estimate_translation(ref, mov, DEFAULT_CONFIG)
    assert result.status is EstimateStatus.FAILED
    assert result.failure_reason is FailureCategory.NO_COMMON_CONTENT
    # ...and must NOT be misclassified as a brightness change
    assert Uncertainty.BRIGHTNESS_CHANGE not in result.uncertainties


def test_shape_mismatch_fails():
    a = np.zeros((32, 32))
    b = np.zeros((32, 48))
    result = estimate_translation(a, b, DEFAULT_CONFIG)
    assert result.status is EstimateStatus.FAILED
    assert result.failure_reason is FailureCategory.SHAPE_MISMATCH


def test_non_finite_input_fails():
    a = np.random.default_rng(0).standard_normal((32, 32))
    b = a.copy()
    b[3, 3] = np.nan
    result = estimate_translation(a, b, DEFAULT_CONFIG)
    assert result.status is EstimateStatus.FAILED
    assert result.failure_reason is FailureCategory.INVALID_IMAGE
