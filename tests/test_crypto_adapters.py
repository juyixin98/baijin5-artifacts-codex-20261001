"""密码适配层测试:协商对称性、AEAD 防篡改、掩码确定性与错误类别。"""

import os

import pytest

from secagg import crypto_adapters as crypto
from secagg.errors import ErrorCategory, SecAggError


def test_key_agreement_symmetric():
    sk_a, pk_a = crypto.generate_keypair()
    sk_b, pk_b = crypto.generate_keypair()
    ka = crypto.key_agreement(sk_a, pk_b, crypto.HKDF_INFO_MASK)
    kb = crypto.key_agreement(sk_b, pk_a, crypto.HKDF_INFO_MASK)
    assert ka == kb and len(ka) == 32


def test_different_info_gives_different_keys():
    sk_a, pk_a = crypto.generate_keypair()
    sk_b, pk_b = crypto.generate_keypair()
    k1 = crypto.key_agreement(sk_a, pk_b, b"info-1")
    k2 = crypto.key_agreement(sk_a, pk_b, b"info-2")
    assert k1 != k2


def test_aead_roundtrip_and_tamper_detection():
    key = os.urandom(32)
    ct = crypto.aead_encrypt(key, b"share-payload", b"aad")
    assert crypto.aead_decrypt(key, ct, b"aad") == b"share-payload"

    tampered = bytearray(ct)
    tampered[-1] ^= 1
    with pytest.raises(SecAggError) as exc:
        crypto.aead_decrypt(key, bytes(tampered), b"aad")
    assert exc.value.category is ErrorCategory.COMPUTATION_FAILURE
    assert exc.value.code == "decrypt_failed"


def test_aead_wrong_aad_fails():
    key = os.urandom(32)
    ct = crypto.aead_encrypt(key, b"data", b"right-aad")
    with pytest.raises(SecAggError) as exc:
        crypto.aead_decrypt(key, ct, b"wrong-aad")
    assert exc.value.category is ErrorCategory.COMPUTATION_FAILURE


def test_mask_expansion_deterministic_and_in_range():
    seed = os.urandom(32)
    m1 = crypto.expand_mask(seed, 100)
    m2 = crypto.expand_mask(seed, 100)
    assert m1 == m2
    assert len(m1) == 100
    assert all(0 <= v < (1 << 64) for v in m1)


def test_mask_depends_on_seed():
    m1 = crypto.expand_mask(os.urandom(32), 16)
    m2 = crypto.expand_mask(os.urandom(32), 16)
    assert m1 != m2


def test_bad_seed_length_is_input_error():
    with pytest.raises(SecAggError) as exc:
        crypto.expand_mask(b"short", 4)
    assert exc.value.category is ErrorCategory.INPUT_ERROR
