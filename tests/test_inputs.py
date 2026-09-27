"""输入模块测试：断言具体的校验类别与规范化结果。"""
import pytest

from irsolver.inputs import (
    MAX_DIMENSION,
    InputValidationError,
    parse_matrix,
    parse_system,
)


def test_valid_system_preserves_decimal_text():
    A, B = parse_system([["1.5", 2], ["0", "4"]], [["1e-3"], [2.5]])
    assert A.text == (("1.5", "2"), ("0", "4"))
    assert B.text == (("0.001",), ("2.5",))
    assert A.n_rows == 2 and B.n_cols == 1
    assert A.to_float64().tolist() == [[1.5, 2.0], [0.0, 4.0]]


def test_rejects_non_square():
    with pytest.raises(InputValidationError) as exc:
        parse_system([["1", "2", "3"], ["4", "5", "6"]], [["1"], ["2"]])
    assert exc.value.reason == "not_square"


def test_rejects_ragged_rows():
    with pytest.raises(InputValidationError) as exc:
        parse_system([["1", "2"], ["3"]], [["1"], ["2"]])
    assert exc.value.reason == "ragged"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "nan", "-inf", "NaN"])
def test_rejects_non_finite(bad):
    with pytest.raises(InputValidationError) as exc:
        parse_system([["1", bad], ["0", "1"]], [["1"], ["2"]])
    assert exc.value.reason == "non_finite"


def test_rejects_dimension_mismatch():
    with pytest.raises(InputValidationError) as exc:
        parse_system([["1", "0"], ["0", "1"]], [["1"], ["2"], ["3"]])
    assert exc.value.reason == "dimension_mismatch"


def test_rejects_empty_and_scalar():
    with pytest.raises(InputValidationError) as exc:
        parse_system([], [["1"]])
    assert exc.value.reason == "invalid_shape"
    with pytest.raises(InputValidationError) as exc:
        parse_matrix("not-a-matrix", "a")
    assert exc.value.reason == "invalid_shape"


def test_rejects_bool_and_bad_string():
    with pytest.raises(InputValidationError) as exc:
        parse_matrix([[True]], "a")
    assert exc.value.reason == "invalid_entry"
    with pytest.raises(InputValidationError) as exc:
        parse_matrix([["abc"]], "a")
    assert exc.value.reason == "invalid_entry"


def test_rejects_oversized_matrix():
    big = [["1"] * (MAX_DIMENSION + 1)] * (MAX_DIMENSION + 1)
    with pytest.raises(InputValidationError) as exc:
        parse_matrix(big, "a")
    assert exc.value.reason == "too_large"
