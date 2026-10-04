"""HKDF-SHA256 adapters over mature crypto backends.

No hash or HMAC primitive is implemented here. Two independent backends are
provided:

- CryptographyHkdf  — the `cryptography` package (service default)
- PyCryptodomeHkdf  — the `pycryptodome` package (independent cross-check)

Both expose the same narrow contract: hkdf_sha256(ikm, salt, info, length).
Length is bounded by the HKDF-SHA256 algorithmic maximum (255 * 32 bytes);
exceeding it is a resource_exhausted error, not a silent truncation.
Backend exceptions are wrapped as computation_failure so callers never see
library-specific exception types.
"""

from __future__ import annotations

from typing import Protocol

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from Crypto.Hash import SHA256 as _PCSHA256
from Crypto.Protocol.KDF import HKDF as _PCHKDF

from .errors import ComputationError, ResourceExhaustedError

#: RFC 5869: L <= 255 * HashLen; HashLen(SHA-256) = 32.
MAX_OKM_LEN = 255 * 32


class HkdfBackend(Protocol):
    """Narrow HKDF-SHA256 contract used by the service."""

    name: str

    def hkdf_sha256(self, ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
        ...


def _check_length(length: int) -> None:
    if not isinstance(length, int) or isinstance(length, bool):
        raise ResourceExhaustedError(
            "derivation length must be an integer",
            details={"type": type(length).__name__},
        )
    if length < 1:
        raise ResourceExhaustedError(
            "derivation length must be positive", details={"length": length}
        )
    if length > MAX_OKM_LEN:
        raise ResourceExhaustedError(
            "derivation length exceeds HKDF-SHA256 maximum",
            details={"length": length, "max": MAX_OKM_LEN},
        )


class CryptographyHkdf:
    """HKDF-SHA256 via the `cryptography` package."""

    name = "cryptography"

    def hkdf_sha256(self, ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
        _check_length(length)
        try:
            hkdf = HKDF(
                algorithm=hashes.SHA256(),
                length=length,
                salt=salt,
                info=info,
            )
            return hkdf.derive(bytes(ikm))
        except Exception as exc:  # backend failure, never caller-visible type
            raise ComputationError(
                "HKDF backend failed",
                details={"backend": self.name, "error": type(exc).__name__},
            ) from exc


class PyCryptodomeHkdf:
    """HKDF-SHA256 via the `pycryptodome` package (independent backend)."""

    name = "pycryptodome"

    def hkdf_sha256(self, ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
        _check_length(length)
        try:
            return _PCHKDF(
                master=bytes(ikm),
                key_len=length,
                salt=bytes(salt),
                hashmod=_PCSHA256,
                context=bytes(info),
            )
        except Exception as exc:
            raise ComputationError(
                "HKDF backend failed",
                details={"backend": self.name, "error": type(exc).__name__},
            ) from exc
