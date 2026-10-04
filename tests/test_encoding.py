"""Encoding layer tests: hand-computed residues, bounds, ambiguous zone."""

import pytest

from app.encoding import (
    EncodingParams,
    check_coefficient,
    decode_signed,
    encode_signed,
)
from app.errors import ErrorCategory, PaillierServiceError
from tests.reference import reference_decode, reference_encode

PARAMS = EncodingParams(
    max_plaintext_abs=100, max_coefficient_abs=10, max_aggregate_abs=500
)
# Small fake modulus so residues can be checked by hand.
N = 10_000


def test_encode_positive_hand_computed():
    # 7 mod 10000 = 7
    assert encode_signed(7, N, PARAMS) == 7


def test_encode_negative_hand_computed():
    # -5 mod 10000 = 9995
    assert encode_signed(-5, N, PARAMS) == 9995


def test_encode_boundary_values():
    assert encode_signed(100, N, PARAMS) == 100
    assert encode_signed(-100, N, PARAMS) == N - 100


def test_encode_out_of_range_raises_category():
    for bad in (101, -101, 10**6):
        with pytest.raises(PaillierServiceError) as excinfo:
            encode_signed(bad, N, PARAMS)
        assert excinfo.value.category == ErrorCategory.PLAINTEXT_OUT_OF_RANGE


def test_encode_rejects_non_integer():
    with pytest.raises(PaillierServiceError) as excinfo:
        encode_signed(1.5, N, PARAMS)  # type: ignore[arg-type]
    assert excinfo.value.category == ErrorCategory.PLAINTEXT_OUT_OF_RANGE


def test_decode_positive_and_negative_hand_computed():
    assert decode_signed(7, N, PARAMS) == 7
    assert decode_signed(9995, N, PARAMS) == -5
    # boundary of the guaranteed range
    assert decode_signed(500, N, PARAMS) == 500
    assert decode_signed(N - 500, N, PARAMS) == -500


def test_decode_ambiguous_zone_raises_not_negative():
    # 501..9499 is the ambiguous zone for bound=500, n=10000.
    # A residue here means wraparound occurred; it must NOT be read as
    # an ordinary negative number.
    for raw in (501, 5000, N - 501):
        with pytest.raises(PaillierServiceError) as excinfo:
            decode_signed(raw, N, PARAMS)
        assert excinfo.value.category == ErrorCategory.DECODE_AMBIGUOUS


def test_decode_matches_independent_reference():
    for raw in (0, 1, 499, 500, N - 500, N - 1):
        expected = reference_decode(raw, N, PARAMS.max_aggregate_abs)
        assert decode_signed(raw, N, PARAMS) == expected
    for raw in (501, 9000, N - 501):
        assert reference_decode(raw, N, PARAMS.max_aggregate_abs) is None
        with pytest.raises(PaillierServiceError):
            decode_signed(raw, N, PARAMS)


def test_encode_matches_independent_reference():
    for value in (-100, -1, 0, 1, 100):
        assert encode_signed(value, N, PARAMS) == reference_encode(value, N)


def test_coefficient_bounds():
    check_coefficient(10, PARAMS)
    check_coefficient(-10, PARAMS)
    check_coefficient(0, PARAMS)
    for bad in (11, -11):
        with pytest.raises(PaillierServiceError) as excinfo:
            check_coefficient(bad, PARAMS)
        assert excinfo.value.category == ErrorCategory.COEFFICIENT_OUT_OF_RANGE


def test_params_validation_against_modulus():
    # 2 * max_aggregate_abs >= n must be rejected.
    too_big = EncodingParams(
        max_plaintext_abs=100, max_coefficient_abs=10, max_aggregate_abs=5000
    )
    with pytest.raises(PaillierServiceError) as excinfo:
        too_big.validate_against_modulus(N)
    assert excinfo.value.category == ErrorCategory.ENCODING_PARAM_INVALID


def test_params_self_validation():
    bad = EncodingParams(
        max_plaintext_abs=1000, max_coefficient_abs=10, max_aggregate_abs=10
    )
    with pytest.raises(PaillierServiceError) as excinfo:
        bad.validate_self()
    assert excinfo.value.category == ErrorCategory.ENCODING_PARAM_INVALID
