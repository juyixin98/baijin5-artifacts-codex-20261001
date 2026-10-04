"""Independent verifier, backed by PyCryptodome.

The release path does not trust the core adapter alone: every chunk tag
is re-checked with a *different* AEAD implementation before plaintext is
published.  The same module is used to generate the reference test
vectors, so the expected answers in the test-suite are not produced by
the implementation under test.
"""

from __future__ import annotations

from Crypto.Cipher import ChaCha20_Poly1305

from . import protocol


class IndependentVerifier:
    """ChaCha20-Poly1305 from PyCryptodome, used as a second opinion."""

    backend_name = "pycryptodome/ChaCha20-Poly1305"

    def encrypt(self, key: bytes, nonce: bytes, aad: bytes, plaintext: bytes) -> bytes:
        """Return ``ciphertext || tag`` (reference implementation)."""
        cipher = ChaCha20_Poly1305.new(key=key, nonce=nonce)
        cipher.update(aad)
        ct, tag = cipher.encrypt_and_digest(plaintext)
        return ct + tag

    def verify(self, key: bytes, nonce: bytes, aad: bytes, blob: bytes) -> bool:
        """Return True iff ``blob`` authenticates under key/nonce/aad.

        Plaintext is deliberately discarded: this is a verification
        oracle, not a decryption path.
        """
        ct, tag = protocol.split_ciphertext(blob)
        cipher = ChaCha20_Poly1305.new(key=key, nonce=nonce)
        cipher.update(aad)
        cipher.decrypt(ct)
        try:
            cipher.verify(tag)
        except ValueError:
            return False
        return True
