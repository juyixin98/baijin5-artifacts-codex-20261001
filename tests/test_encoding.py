"""编码层测试:小整数手算复核,覆盖负值、回绕拒绝、值域校验。"""
import pytest

from app import encoding
from app.errors import ErrorCategory, ServiceError
from tests.reference import reference_encode

# 手算夹具:n = 35 = 5 * 7, n//2 = 17
N = 35


@pytest.mark.parametrize("value,expected", [
    (0, 0),
    (3, 3),
    (-3, 32),    # -3 mod 35 = 32,手算
    (17, 17),
    (-18, 17),   # -18 + 35 = 17
    (35, 0),
])
def test_encode_signed_hand_computed(value, expected, rlog):
    got = encoding.encode_signed(value, N)
    rlog("encode", value=value, n=N, expected=expected, actual=got,
         basis="手算 x mod n")
    assert got == expected
    assert got == reference_encode(value, N)


@pytest.mark.parametrize("residue,bound,expected", [
    (3, 10, 3),
    (32, 10, -3),    # 32 > 17 -> 32 - 35 = -3
    (0, 10, 0),
    (16, 16, 16),  # 边界:恰在上界(bound 必须 < n//2 = 17)
])
def test_decode_signed_within_bound(residue, bound, expected, rlog):
    got = encoding.decode_signed(residue, N, bound)
    rlog("decode", residue=residue, n=N, bound=bound, expected=expected,
         actual=got, basis="剩余居中后 |v|<=bound")
    assert got == expected


def test_decode_refuses_wraparound(rlog):
    # 剩余 18 -> 居中 -17,|v| > bound=10:必须拒绝而非返回 -17
    rlog("decode-overflow", residue=18, n=N, bound=10,
         basis="居中值 -17 超出上界,模回绕不得解释为普通负值")
    with pytest.raises(ServiceError) as exc_info:
        encoding.decode_signed(18, N, 10)
    assert exc_info.value.category is ErrorCategory.OVERFLOW_DETECTED


def test_decode_rejects_bound_ge_half_n():
    with pytest.raises(ServiceError) as exc_info:
        encoding.decode_signed(3, N, 17 + 1)
    assert exc_info.value.category is ErrorCategory.VALIDATION


def test_decode_rejects_residue_out_of_range():
    with pytest.raises(ServiceError) as exc_info:
        encoding.decode_signed(35, N, 10)
    assert exc_info.value.category is ErrorCategory.VALIDATION


def test_validate_plaintext_and_weight():
    encoding.validate_plaintext(100, 100)
    encoding.validate_plaintext(-100, 100)
    with pytest.raises(ServiceError) as e1:
        encoding.validate_plaintext(101, 100)
    assert e1.value.category is ErrorCategory.OUT_OF_RANGE
    encoding.validate_weight(-50, 50)
    with pytest.raises(ServiceError) as e2:
        encoding.validate_weight(51, 50)
    assert e2.value.category is ErrorCategory.OUT_OF_RANGE
