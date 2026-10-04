"""Mature-AEAD adapters behind one interface.

Two independent implementations of AES-256-GCM are exposed so the test suite
can seal with one library and open with the other (and vice versa).  Neither
implementation here is novel:

* ``cryptography``  - OpenSSL EVP via the ``cryptography`` package
* ``pycryptodome``  - the ``Crypto.Cipher.AES`` GCM mode

The interface is deliberately tiny: seal -> ciphertext-with-tag, open ->
plaintext or ``InvalidTag``.  All protocol policy (nonce derivation, AAD)
lives in :mod:`app.core.protocol`, never in a backend.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .errors import AuthenticationError, KeyMaterialError
from .protocol import NONCE_LEN, TAG_LEN

KEY_LEN = 32  # AES-256


class AEADBackend(ABC):
    name = "abstract"

    @staticmethod
    def validate_key(key: bytes) -> None:
        if not isinstance(key, (bytes, bytearray)):
            raise KeyMaterialError("AEAD key must be bytes")
        if len(key) != KEY_LEN:
            raise KeyMaterialError(
                f"AES key must be {KEY_LEN} bytes", key_len=len(key))

    @abstractmethod
    def seal(self, key: bytes, nonce: bytes, plaintext: bytes,
             aad: bytes) -> bytes:
        """Return ciphertext || 16-byte GCM tag."""

    @abstractmethod
    def open(self, key: bytes, nonce: bytes, ciphertext: bytes,
             aad: bytes) -> bytes:
        """Verify tag and return plaintext; raise AuthenticationError."""


def _check_nonce(nonce: bytes) -> None:
    if len(nonce) != NONCE_LEN:
        raise AuthenticationError("bad nonce length", nonce_len=len(nonce))


def _check_ciphertext(ciphertext: bytes) -> None:
    if len(ciphertext) < TAG_LEN:
        raise AuthenticationError("ciphertext shorter than tag")


class CryptographyBackend(AEADBackend):
    """AES-GCM via the `cryptography` package (OpenSSL)."""

    name = "cryptography"

    def seal(self, key: bytes, nonce: bytes, plaintext: bytes,
             aad: bytes) -> bytes:
        self.validate_key(key)
        _check_nonce(nonce)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(bytes(key)).encrypt(bytes(nonce), bytes(plaintext),
                                          bytes(aad))

    def open(self, key: bytes, nonce: bytes, ciphertext: bytes,
             aad: bytes) -> bytes:
        self.validate_key(key)
        _check_nonce(nonce)
        _check_ciphertext(ciphertext)
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        try:
            return AESGCM(bytes(key)).decrypt(bytes(nonce), bytes(ciphertext),
                                              bytes(aad))
        except InvalidTag as exc:
            raise AuthenticationError(
                "AES-GCM authentication failed") from exc


class PyCryptodomeBackend(AEADBackend):
    """AES-GCM via PyCryptodome (independent implementation for verification)."""

    name = "pycryptodome"

    def seal(self, key: bytes, nonce: bytes, plaintext: bytes,
             aad: bytes) -> bytes:
        self.validate_key(key)
        _check_nonce(nonce)
        from Crypto.Cipher import AES
        cipher = AES.new(bytes(key), AES.MODE_GCM, nonce=bytes(nonce))
        cipher.update(bytes(aad))
        ct, tag = cipher.encrypt_and_digest(bytes(plaintext))
        return ct + tag

    def open(self, key: bytes, nonce: bytes, ciphertext: bytes,
             aad: bytes) -> bytes:
        self.validate_key(key)
        _check_nonce(nonce)
        _check_ciphertext(ciphertext)
        from Crypto.Cipher import AES
        body, tag = ciphertext[:-TAG_LEN], ciphertext[-TAG_LEN:]
        cipher = AES.new(bytes(key), AES.MODE_GCM, nonce=bytes(nonce))
        cipher.update(bytes(aad))
        try:
            return cipher.decrypt_and_verify(bytes(body), bytes(tag))
        except ValueError as exc:
            # PyCryptodome raises "MAC check failed" on tag mismatch.
            raise AuthenticationError(
                "AES-GCM authentication failed") from exc


_BACKENDS: dict[str, type[AEADBackend]] = {
    CryptographyBackend.name: CryptographyBackend,
    PyCryptodomeBackend.name: PyCryptodomeBackend,
}


def get_backend(name: str) -> AEADBackend:
    try:
        return _BACKENDS[name]()
    except KeyError:
        raise KeyMaterialError("unknown AEAD backend",
                               backend=name,
                               available=sorted(_BACKENDS))
