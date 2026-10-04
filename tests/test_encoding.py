"""编码层测试:模数范围、防溢出上界、往返一致性、错误类别。"""

import pytest

from secagg.encoding import (
    HALF_MODULUS,
    MODULUS,
    EncodingParams,
    add_vectors_mod,
    decode_scalar,
    decode_vector,
    encode_scalar,
    encode_vector,
)
from secagg.errors import ErrorCategory, SecAggError

PARAMS = EncodingParams(max_clients=4).validate()


def test_roundtrip_positive_negative_zero():
    for x in [0.0, 1.5, -2.25, 123456.789, -98765.4321]:
        decoded = decode_scalar(encode_scalar(x, PARAMS), PARAMS)
        assert abs(decoded - x) <= 0.5 / PARAMS.scale + 1e-12


def test_element_out_of_range_is_input_error():
    too_big = (PARAMS.elem_bound + 1) / PARAMS.scale
    with pytest.raises(SecAggError) as exc:
        encode_scalar(too_big, PARAMS)
    assert exc.value.category is ErrorCategory.INPUT_ERROR
    assert exc.value.code == "elem_out_of_range"


def test_negative_overflow_rejected_symmetrically():
    too_small = -(PARAMS.elem_bound + 1) / PARAMS.scale
    with pytest.raises(SecAggError) as exc:
        encode_scalar(too_small, PARAMS)
    assert exc.value.category is ErrorCategory.INPUT_ERROR


def test_sum_overflow_bound_enforced_at_config():
    # max_clients * elem_bound > R/2 - 1 -> 资源耗尽, 拒绝建参
    with pytest.raises(SecAggError) as exc:
        EncodingParams(elem_bound=HALF_MODULUS, max_clients=2).validate()
    assert exc.value.category is ErrorCategory.RESOURCE_EXHAUSTED
    assert exc.value.code == "overflow_risk"


def test_modular_wraparound_decodes_to_negative():
    # -1 的补码表示为 R-1, 解码应还原为约 -1/scale 的负值
    encoded = encode_scalar(-1 / PARAMS.scale, PARAMS)
    assert encoded == MODULUS - 1
    assert decode_scalar(encoded, PARAMS) < 0


def test_vector_length_limit_is_resource_error():
    params = EncodingParams(max_clients=4, max_vector_len=8).validate()
    with pytest.raises(SecAggError) as exc:
        encode_vector([1.0] * 9, params)
    assert exc.value.category is ErrorCategory.RESOURCE_EXHAUSTED


def test_add_mod_wraps_and_decodes():
    a = encode_vector([3.0], PARAMS)
    b = encode_vector([-5.0], PARAMS)
    total = add_vectors_mod(a, b)
    assert abs(decode_vector(total, PARAMS)[0] - (-2.0)) < 1e-6
