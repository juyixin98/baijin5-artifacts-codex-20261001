import numpy as np
import pytest

from geodesic_recon.contracts import validate_pair
from geodesic_recon.errors import ContractViolation


def test_valid_pair_passes_unclipped():
    pair = validate_pair([[1, 2], [3, 4]], [[10, 10], [10, 10]])
    assert not pair.clipped
    assert pair.marker.dtype == np.float64
    np.testing.assert_array_equal(pair.marker, [[1, 2], [3, 4]])


def test_marker_exceeds_mask_rejected_with_category():
    with pytest.raises(ContractViolation) as exc:
        validate_pair([[5.0]], [[3.0]], on_violation="reject")
    assert exc.value.category == "MARKER_EXCEEDS_MASK"
    assert "1 pixel" in exc.value.detail


def test_marker_exceeds_mask_clipped_when_requested():
    pair = validate_pair([[5.0, 1.0]], [[3.0, 2.0]], on_violation="clip")
    assert pair.clipped
    np.testing.assert_array_equal(pair.marker, [[3.0, 1.0]])


def test_shape_mismatch():
    with pytest.raises(ContractViolation) as exc:
        validate_pair([[1, 2]], [[1], [2]])
    assert exc.value.category == "SHAPE_MISMATCH"


@pytest.mark.parametrize(
    "marker, category",
    [
        ([[[1.0]]], "BAD_RANK"),
        ([[float("nan")]], "NON_FINITE"),
        ([[float("inf")]], "NON_FINITE"),
        ([[1 + 2j]], "DTYPE_UNSUPPORTED"),
    ],
)
def test_malformed_marker_categories(marker, category):
    with pytest.raises(ContractViolation) as exc:
        validate_pair(marker, [[1.0]])
    assert exc.value.category == category


def test_empty_image_rejected():
    with pytest.raises(ContractViolation) as exc:
        validate_pair(np.zeros((0, 3)), np.zeros((0, 3)))
    assert exc.value.category == "EMPTY_IMAGE"


def test_size_limit_enforced():
    with pytest.raises(ContractViolation) as exc:
        validate_pair(np.zeros((10, 10)), np.zeros((10, 10)), max_pixels=50)
    assert exc.value.category == "IMAGE_TOO_LARGE"


def test_ragged_input_rejected():
    with pytest.raises(ContractViolation) as exc:
        validate_pair([[1, 2], [3]], [[1, 2], [3, 4]])
    assert exc.value.category in ("MALFORMED_IMAGE", "BAD_RANK")


def test_bad_policy_rejected():
    with pytest.raises(ContractViolation) as exc:
        validate_pair([[1.0]], [[1.0]], on_violation="ignore")
    assert exc.value.category == "BAD_POLICY"


def test_bool_and_int_inputs_accepted():
    pair = validate_pair([[True, False]], [[1, 1]])
    np.testing.assert_array_equal(pair.marker, [[1.0, 0.0]])
