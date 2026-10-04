"""Core AEAD adapter, backed by ``cryptography`` (ChaCha20-Poly1305).

This is the only module the service uses to encrypt and decrypt.  It is
intentionally thin: all binding of message identity / sequence / final
flag lives in :mod:`sae.protocol` (nonce + AAD), so the adapter can be
swapped or cross-checked without touching protocol logic.
"""

from __future__ import annotations

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from . import protocol


class CoreAEAD:
    """ChaCha20-Poly1305 AEAD from the ``cryptography`` package."""

    backend_name = "cryptography/ChaCha20Poly1305"

    def encrypt(self, key: bytes, nonce: bytes, aad: bytes, plaintext: bytes) -> bytes:
        """Return ``ciphertext || tag``."""
        if len(key) != protocol.KEY_LEN:
            raise ValueError("bad key length")
        if len(nonce) != protocol.NONCE_LEN:
            raise ValueError("bad nonce length")
        return ChaCha20Poly1305(key).encrypt(nonce, plaintext, aad)

    def decrypt(self, key: bytes, nonce: bytes, aad: bytes, blob: bytes) -> bytes:
        """Verify and open ``ciphertext || tag``.

        Raises :class:`TagVerificationError` (never returns plaintext)
        when authentication fails.
        """
        if len(key) != protocol.KEY_LEN:
            raise ValueError("bad key length")
        if len(nonce) != protocol.NONCE_LEN:
            raise ValueError("bad nonce length")
        try:
            return ChaCha20Poly1305(key).decrypt(nonce, blob, aad)
        except InvalidTag as exc:
            raise TagVerificationError("AEAD tag verification failed") from exc


class TagVerificationError(Exception):
    """Raised when AEAD authentication fails."""
