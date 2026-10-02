"""Sub-pixel accuracy tests against fixture ground truth and the independent
spatial-domain reference implementation (tests/reference_impl.py)."""
import numpy as np

from app.config import DEFAULT_CONFIG
from app.kernel import estimate_translation
from app.kernel.types import EstimateStatus
from tests.reference_impl import best_integer_shift_ncc


def _shift_of(result):
    assert result.shift is not None
    return np.array(result.shift)


def test_integer_shift_matches_ground_truth_and_reference(pair):
    ref, mov, entry = pair("integer_shift")
    result = estimate_translation(ref, mov, DEFAULT_CONFIG)
    assert result.status is EstimateStatus.OK
    gt = np.array(entry["ground_truth_shift"])
    assert np.all(np.abs(_shift_of(result) - gt) < 0.15)

    # independent brute-force spatial reference agrees on the integer part
    (rdy, rdx), ncc = best_integer_shift_ncc(ref, mov, max_shift=16)
    assert (rdy, rdx) == (5, -3)
    assert ncc > 0.99
    assert np.all(np.abs(_shift_of(result) - np.array([rdy, rdx])) < 0.5)


def test_subpixel_shift_accuracy(pair):
    ref, mov, entry = pair("subpixel_shift")
    result = estimate_translation(ref, mov, DEFAULT_CONFIG)
    assert result.status is EstimateStatus.OK
    gt = np.array(entry["ground_truth_shift"])
    err = np.abs(_shift_of(result) - gt)
    assert np.all(err < 0.15), f"subpixel error {err} exceeds 0.15 px"


def test_subpixel_refinements_agree(pair):
    """The two independent refinements (direct DFT vs parabolic) must agree."""
    ref, mov, _ = pair("subpixel_shift")
    result = estimate_translation(ref, mov, DEFAULT_CONFIG)
    assert result.confidence["refine_disagreement_px"] < 0.1


def test_confidence_block_is_populated(pair):
    ref, mov, _ = pair("integer_shift")
    result = estimate_translation(ref, mov, DEFAULT_CONFIG)
    conf = result.confidence
    for key in ("peak_height", "peak_to_sidelobe", "surface_contrast",
                "refine_disagreement_px", "zero_bin_fraction",
                "overlap_ncc", "overlap_fraction"):
        assert key in conf, key
    assert 0.0 < conf["peak_height"] <= 1.0
    assert conf["overlap_ncc"] > 0.9
    assert conf["overlap_fraction"] > 0.85
