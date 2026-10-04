"""Unit tests for AEAD backends: KAT and cross-implementation agreement."""

from __future__ import annotations

import base64

import pytest

from app.core.crypto import (
    CryptographyBackend, PyCryptodomeBackend, get_backend,
)
from app.core.errors import AuthenticationError, KeyMaterialError
from app.core.protocol import derive_nonce, encode_aad

pytestmark = pytest.mark.unit


def _nonce(bundle):
    return derive_nonce(bundle.nonce_key, "m", 0, True)


def test_gcm_known_answer_both_backends(gcm_kat):
    key = base64.b64decode(gcm_kat["key_b64"])
    nonce = base64.b64decode(gcm_kat["nonce_b64"])
    aad = base64.b64decode(gcm_kat["aad_b64"])
    pt = base64.b64decode(gcm_kat["plaintext_b64"])
    expected_ct = base64.b64decode(gcm_kat["ciphertext_tag_b64"])

    assert CryptographyBackend().seal(key, nonce, pt, aad) == expected_ct
    assert PyCryptodomeBackend().seal(key, nonce, pt, aad) == expected_ct
    assert CryptographyBackend().open(key, nonce, expected_ct, aad) == pt
    assert PyCryptodomeBackend().open(key, nonce, expected_ct, aad) == pt


@pytest.mark.parametrize("sealer", [CryptographyBackend, PyCryptodomeBackend])
@pytest.mark.parametrize("opener_cls", [CryptographyBackend, PyCryptodomeBackend])
def test_backends_are_interoperable(fixture_keys, sealer, opener_cls):
    # Seal with one mature implementation, open with the other.
    sealer_b, opener_b = sealer(), opener_cls()
    nonce = derive_nonce(fixture_keys.nonce_key, "interop", 2, True)
    aad = encode_aad("interop", 2, 3, True, 42, 10)
    ct = sealer_b.seal(fixture_keys.aead_key, nonce, b"0123456789", aad)
    assert opener_b.open(fixture_keys.aead_key, nonce, ct, aad) == b"0123456789"


@pytest.mark.parametrize("backend", [CryptographyBackend(), PyCryptodomeBackend()])
def test_tampered_ciphertext_is_rejected(fixture_keys, backend):
    nonce = _nonce(fixture_keys)
    aad = encode_aad("m", 0, 1, True, 3, 3)
    ct = bytearray(backend.seal(fixture_keys.aead_key, nonce, b"abc", aad))
    ct[0] ^= 0x01
    with pytest.raises(AuthenticationError) as ei:
        backend.open(fixture_keys.aead_key, nonce, bytes(ct), aad)
    assert ei.value.category == "auth_failed"


@pytest.mark.parametrize("backend", [CryptographyBackend(), PyCryptodomeBackend()])
def test_tampered_aad_is_rejected(fixture_keys, backend):
    nonce = _nonce(fixture_keys)
    aad = encode_aad("m", 0, 1, True, 3, 3)
    ct = backend.seal(fixture_keys.aead_key, nonce, b"abc", aad)
    bogus_aad = encode_aad("m", 0, 1, True, 4, 3)  # total length lying
    with pytest.raises(AuthenticationError):
        backend.open(fixture_keys.aead_key, nonce, ct, bogus_aad)


@pytest.mark.parametrize("backend", [CryptographyBackend(), PyCryptodomeBackend()])
def test_wrong_key_is_rejected(fixture_keys, backend):
    other = bytes(reversed(fixture_keys.aead_key))
    nonce = _nonce(fixture_keys)
    aad = encode_aad("m", 0, 1, True, 0, 0)
    ct = backend.seal(fixture_keys.aead_key, nonce, b"", aad)
    with pytest.raises(AuthenticationError):
        backend.open(other, nonce, ct, aad)


def test_invalid_key_length_rejected():
    with pytest.raises(KeyMaterialError) as ei:
        CryptographyBackend().seal(b"short", b"\x00" * 12, b"", b"")
    assert ei.value.category == "key_material"


def test_get_backend_unknown():
    with pytest.raises(KeyMaterialError):
        get_backend("rot13")
