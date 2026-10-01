"""Invalid p-values are rejected with the INPUT category; valid boundary
values are accepted.  Boolean and non-numeric inputs must not be coerced.
"""

import math

import pytest

from app.contracts import LORD3State, step
from app.errors import (
    INVALID_PVALUE,
    InvalidHypothesisIdError,
    InvalidPValueError,
    LORD3Error,
)


@pytest.mark.parametrize(
    "bad",
    [-0.0000001, 1.0000001, float("nan"), float("inf"), float("-inf"),
     "0.5", None, [0.5], {"p": 0.5}],
)
def test_invalid_p_values_raise_typed_input_error(bad):
    state = LORD3State.initial()
    with pytest.raises(InvalidPValueError) as exc:
        step(state, "H1", bad)
    assert exc.value.error_code.code == "E1001"
    assert exc.value.error_code.category.value == "INPUT"
    assert isinstance(exc.value, LORD3Error)


def test_bool_is_not_a_p_value_even_though_it_is_an_int_subclass():
    with pytest.raises(InvalidPValueError):
        step(LORD3State.initial(), "H1", True)


@pytest.mark.parametrize("good", [0.0, 1.0, 5e-324])
def test_boundary_p_values_are_accepted(good):
    _, d = step(LORD3State.initial(), "H1", good)
    assert math.isfinite(d.threshold)


@pytest.mark.parametrize(
    "hid",
    ["", " ", "x y", "tab\there", 123, None, "x" * 257, " leading",
     "trailing "],
)
def test_invalid_hypothesis_ids_are_rejected(hid):
    with pytest.raises(InvalidHypothesisIdError) as exc:
        step(LORD3State.initial(), hid, 0.5)
    assert exc.value.error_code.code == "E1002"
    assert exc.value.error_code.category.value == "INPUT"


def test_error_payload_is_machine_readable():
    try:
        step(LORD3State.initial(), "H1", float("nan"))
    except InvalidPValueError as exc:
        payload = exc.to_dict()["error"]
        assert payload["code"] == INVALID_PVALUE.code
        assert payload["category"] == "INPUT"
        assert "received" in payload["details"]
    else:  # pragma: no cover
        pytest.fail("expected InvalidPValueError")
