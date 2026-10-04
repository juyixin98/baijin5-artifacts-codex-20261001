"""Crypto adapter tests against hand-computed and independent references."""

import pytest

from app import crypto_adapter
from app.errors import ErrorCategory, PaillierServiceError
from tests.reference import (
    reference_aggregate_ciphertext,
    reference_decrypt,
    reference_encode,
)


@pytest.fixture(scope="module")
def keypair():
    return crypto_adapter.generate_keypair(1024)


def test_encrypt_decrypt_roundtrip(keypair):
    pub, priv = keypair.public_key, keypair.private_key
    for value in (0, 1, 42, pub.n - 1):
        c = crypto_adapter.encrypt_encoded(pub, value)
        assert crypto_adapter.decrypt_to_encoded(priv, c) == value


def test_addition_matches_plaintext_reference(keypair):
    pub, priv = keypair.public_key, keypair.private_key
    a, b = 3, 4  # hand-computed expectation: 3 + 4 = 7
    ca = crypto_adapter.encrypt_encoded(pub, a)
    cb = crypto_adapter.encrypt_encoded(pub, b)
    csum = crypto_adapter.add_ciphertexts(pub, ca, cb)
    assert crypto_adapter.decrypt_to_encoded(priv, csum) == 7


def test_addition_matches_raw_modular_reference(keypair):
    pub = keypair.public_key
    ca = crypto_adapter.encrypt_encoded(pub, 11)
    cb = crypto_adapter.encrypt_encoded(pub, 22)
    # Independent reference: ciphertext multiplication mod n^2.
    assert crypto_adapter.add_ciphertexts(pub, ca, cb) == (ca * cb) % (pub.n * pub.n)


def test_negative_scalar_multiplication(keypair):
    pub, priv = keypair.public_key, keypair.private_key
    # E(3) * (-2) must decrypt to -6 mod n = n - 6.
    c = crypto_adapter.encrypt_encoded(pub, 3)
    weighted = crypto_adapter.multiply_by_scalar(pub, c, -2)
    assert crypto_adapter.decrypt_to_encoded(priv, weighted) == pub.n - 6


def test_scalar_multiplication_matches_pow_reference(keypair):
    pub = keypair.public_key
    c = crypto_adapter.encrypt_encoded(pub, 5)
    for k in (0, 1, 7, -3):
        expected = pow(c, k % pub.n, pub.n * pub.n)
        assert crypto_adapter.multiply_by_scalar(pub, c, k) == expected


def test_weighted_sum_matches_independent_aggregate(keypair):
    pub, priv = keypair.public_key, keypair.private_key
    values, weights = [3, -2, 7], [2, 5, -1]
    ciphertexts = [
        crypto_adapter.encrypt_encoded(pub, reference_encode(v, pub.n))
        for v in values
    ]
    running = None
    for c, w in zip(ciphertexts, weights):
        weighted = crypto_adapter.multiply_by_scalar(pub, c, w)
        running = (
            weighted
            if running is None
            else crypto_adapter.add_ciphertexts(pub, running, weighted)
        )
    # Independent reference over the same ciphertexts.
    assert running == reference_aggregate_ciphertext(ciphertexts, weights, pub.n)
    # Hand-computed plaintext: 2*3 + 5*(-2) + (-1)*7 = 6 - 10 - 7 = -11.
    raw = crypto_adapter.decrypt_to_encoded(priv, running)
    assert raw == reference_encode(-11, pub.n)


def test_independent_textbook_decrypt_agrees_with_adapter(keypair):
    pub, priv = keypair.public_key, keypair.private_key
    c = crypto_adapter.encrypt_encoded(pub, 123)
    assert reference_decrypt(c, pub.n, priv.p, priv.q) == 123
    assert crypto_adapter.decrypt_to_encoded(priv, c) == 123


def test_homomorphic_operations_are_deterministic(keypair):
    pub = keypair.public_key
    ca = crypto_adapter.encrypt_encoded(pub, 8)
    cb = crypto_adapter.encrypt_encoded(pub, 9)
    assert crypto_adapter.add_ciphertexts(pub, ca, cb) == crypto_adapter.add_ciphertexts(
        pub, ca, cb
    )
    assert crypto_adapter.multiply_by_scalar(
        pub, ca, 3
    ) == crypto_adapter.multiply_by_scalar(pub, ca, 3)
    assert crypto_adapter.multiply_by_scalar(
        pub, ca, -4
    ) == crypto_adapter.multiply_by_scalar(pub, ca, -4)


def test_validate_ciphertext_rejects_out_of_range(keypair):
    pub = keypair.public_key
    with pytest.raises(PaillierServiceError) as excinfo:
        crypto_adapter.validate_ciphertext(pub, -1)
    assert excinfo.value.category == ErrorCategory.CIPHERTEXT_INVALID
    with pytest.raises(PaillierServiceError) as excinfo:
        crypto_adapter.validate_ciphertext(pub, pub.n * pub.n)
    assert excinfo.value.category == ErrorCategory.CIPHERTEXT_INVALID


def test_key_fingerprint_distinguishes_keys(keypair):
    other = crypto_adapter.generate_keypair(1024)
    fp1 = crypto_adapter.key_fingerprint(keypair.public_key)
    fp2 = crypto_adapter.key_fingerprint(other.public_key)
    assert fp1 != fp2
    assert fp1.startswith("sha256:")
