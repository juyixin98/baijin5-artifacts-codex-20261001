"""Envelope encryption: randomized, authenticated, and strictly separate
from the deterministic blind index (an index is never a ciphertext)."""
import pytest

from sensitive_layer.crypto import envelope
from sensitive_layer.crypto.blind_index import compute_index

KEY = bytes.fromhex("053c2743457ff13a16d3ace29e3cc3e501d825dc48364d1a5dde0d4e74e8bff2")
OTHER_KEY = bytes.fromhex("520e455d90a99a8b4e35ca4f42184cf5c8ad5630400be761330fce015251a0ed")
AAD = b"AAD\x01test-context"


def test_roundtrip():
    blob = envelope.encrypt(KEY, b"alice@example.com", AAD)
    assert envelope.decrypt(KEY, blob, AAD) == b"alice@example.com"


def test_encryption_is_randomized():
    a = envelope.encrypt(KEY, b"same-value", AAD)
    b = envelope.encrypt(KEY, b"same-value", AAD)
    assert a != b  # fresh random nonce per encryption


def test_index_is_deterministic_but_ciphertext_is_not():
    """Guard against using the deterministic index as an encryption substitute."""
    ct1 = envelope.encrypt(KEY, b"v", AAD)
    ct2 = envelope.encrypt(KEY, b"v", AAD)
    idx1 = compute_index(KEY, b"v", 64)
    idx2 = compute_index(KEY, b"v", 64)
    assert ct1 != ct2 and idx1 == idx2


def test_wrong_aad_fails():
    blob = envelope.encrypt(KEY, b"secret", AAD)
    with pytest.raises(envelope.DecryptionError):
        envelope.decrypt(KEY, blob, b"AAD\x01other-context")


def test_wrong_key_fails():
    blob = envelope.encrypt(KEY, b"secret", AAD)
    with pytest.raises(envelope.DecryptionError):
        envelope.decrypt(OTHER_KEY, blob, AAD)


def test_truncated_blob_fails():
    blob = envelope.encrypt(KEY, b"secret", AAD)
    with pytest.raises(envelope.DecryptionError):
        envelope.decrypt(KEY, blob[:8], AAD)


def test_tampered_ciphertext_fails():
    blob = bytearray(envelope.encrypt(KEY, b"secret", AAD))
    blob[-1] ^= 0x01
    with pytest.raises(envelope.DecryptionError):
        envelope.decrypt(KEY, bytes(blob), AAD)
