"""Crypto adapter tests: backend agreement, length bounds, fixed salt role."""

from __future__ import annotations

import os

import pytest

from kds.crypto_adapter import (
    FIXED_SALT,
    MAX_DERIVE_LEN,
    hkdf_sha256,
    hkdf_sha256_reference,
)
from kds.errors import InputError, ResourceExhaustedError


def test_backends_agree_on_random_inputs():
    for _ in range(8):
        ikm = os.urandom(32)
        info = os.urandom(24)
        length = 64
        assert hkdf_sha256(ikm, info, length) == hkdf_sha256_reference(
            ikm, info, length
        )


def test_max_length_is_accepted():
    out = hkdf_sha256(b"\x01" * 32, b"info", MAX_DERIVE_LEN)
    assert len(out) == MAX_DERIVE_LEN


def test_length_above_algorithm_ceiling_is_resource_exhausted():
    with pytest.raises(ResourceExhaustedError):
        hkdf_sha256(b"\x01" * 32, b"info", MAX_DERIVE_LEN + 1)
    with pytest.raises(ResourceExhaustedError):
        hkdf_sha256_reference(b"\x01" * 32, b"info", MAX_DERIVE_LEN + 1)


def test_non_positive_length_is_input_error():
    for bad in (0, -1):
        with pytest.raises(InputError):
            hkdf_sha256(b"\x01" * 32, b"info", bad)


def test_salt_role_is_fixed_by_default():
    """Callers get the protocol-fixed salt unless they go out of their way."""
    ikm, info, length = b"\x02" * 32, b"ctx", 32
    assert hkdf_sha256(ikm, info, length) == hkdf_sha256(
        ikm, info, length, salt=FIXED_SALT
    )
    # A different salt yields a different key — salt is a real HKDF input,
    # which is exactly why the protocol pins it.
    assert hkdf_sha256(ikm, info, length) != hkdf_sha256(
        ikm, info, length, salt=b"other-salt"
    )
