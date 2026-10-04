"""Shamir 门限分享测试:重建正确性、门限语义、错误类别。"""

import os

import pytest

from secagg import shamir
from secagg.errors import ErrorCategory, SecAggError


def test_split_reconstruct_roundtrip():
    secret = os.urandom(32)
    shares = shamir.split_secret(secret, threshold=3, n=5)
    assert len(shares) == 5
    assert shamir.reconstruct_secret(shares[:3], expected_len=32) == secret
    assert shamir.reconstruct_secret(shares[2:5], expected_len=32) == secret


def test_leading_zero_secret_roundtrips_at_fixed_length():
    # 前导零秘密: 不定长重建会丢字节, 定长重建必须还原
    secret = b"\x00\x00" + os.urandom(30)
    shares = shamir.split_secret(secret, threshold=2, n=3)
    assert shamir.reconstruct_secret(shares[:2], expected_len=32) == secret


def test_any_threshold_subset_recovers_same_secret():
    secret = os.urandom(32)
    shares = shamir.split_secret(secret, threshold=2, n=4)
    import itertools

    for combo in itertools.combinations(shares, 2):
        assert shamir.reconstruct_secret(list(combo), expected_len=32) == secret


def test_below_threshold_never_recovers_secret():
    # 信息论意义上门限以下得不到秘密: 要么重建出错误的值,
    # 要么被实现的长度校验识别为计算失败, 两种结果都可接受,
    # 唯独不允许还原出原秘密。
    secret = os.urandom(32)
    shares = shamir.split_secret(secret, threshold=3, n=5)
    try:
        recovered = shamir.reconstruct_secret(shares[:2])
    except SecAggError as exc:
        assert exc.category is ErrorCategory.COMPUTATION_FAILURE
    else:
        assert recovered != secret


def test_duplicate_share_index_is_input_error():
    secret = os.urandom(32)
    shares = shamir.split_secret(secret, threshold=2, n=3)
    with pytest.raises(SecAggError) as exc:
        shamir.reconstruct_secret([shares[0], shares[0]])
    assert exc.value.category is ErrorCategory.INPUT_ERROR


def test_empty_shares_is_computation_failure():
    with pytest.raises(SecAggError) as exc:
        shamir.reconstruct_secret([])
    assert exc.value.category is ErrorCategory.COMPUTATION_FAILURE


def test_too_many_shares_is_resource_error():
    with pytest.raises(SecAggError) as exc:
        shamir.split_secret(os.urandom(32), threshold=2,
                            n=shamir.MAX_SHARES + 1)
    assert exc.value.category is ErrorCategory.RESOURCE_EXHAUSTED


def test_bad_threshold_is_input_error():
    with pytest.raises(SecAggError) as exc:
        shamir.split_secret(os.urandom(32), threshold=0, n=3)
    assert exc.value.category is ErrorCategory.INPUT_ERROR
