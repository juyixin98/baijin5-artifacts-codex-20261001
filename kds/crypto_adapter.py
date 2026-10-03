"""Cryptographic adapter boundary.

All key derivation goes through HKDF-SHA256 as specified by RFC 5869, using
vetted library implementations only — no hash or HMAC primitive is
implemented in this codebase.

Roles are fixed by protocol:

- ``salt`` is a fixed protocol constant (domain separation at the extractor).
  Callers do not choose it.
- ``info`` carries the full derivation context, produced exclusively by
  :mod:`kds.encoding` so context binding is unambiguous.
- ``ikm`` is the parent key (the raw root only at the first tree level).

Two backends are exposed:

- :func:`hkdf_sha256`           – primary, backed by ``cryptography``.
- :func:`hkdf_sha256_reference` – independent reference, backed by
  PyCryptodome, used by tests and the verification script to cross-check the
  primary backend so reference answers are not all produced by the
  implementation under test.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import HKDF as PyCryptoHKDF

from .errors import InputError, ResourceExhaustedError

HASH_LEN = 32
#: RFC 5869 §2.3: L <= 255 * HashLen for HKDF-SHA256.
MAX_DERIVE_LEN = 255 * HASH_LEN

#: Fixed protocol salt. The salt role is fixed by the protocol; all context
#: separation lives in ``info``.
FIXED_SALT = b"kds:v1:fixed-salt"


def _validate_length(length: int) -> None:
    if not isinstance(length, int) or isinstance(length, bool):
        raise InputError("derivation length must be an integer")
    if length <= 0:
        raise InputError("derivation length must be positive", detail=f"got {length}")
    if length > MAX_DERIVE_LEN:
        raise ResourceExhaustedError(
            f"derivation length exceeds the HKDF-SHA256 ceiling of {MAX_DERIVE_LEN} bytes",
            detail=f"requested {length}",
        )


def hkdf_sha256(ikm: bytes, info: bytes, length: int, *, salt: bytes = FIXED_SALT) -> bytes:
    """Derive ``length`` bytes via HKDF-SHA256 (primary backend)."""
    _validate_length(length)
    hkdf = HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info)
    return hkdf.derive(ikm)


def hkdf_sha256_reference(
    ikm: bytes, info: bytes, length: int, *, salt: bytes = FIXED_SALT
) -> bytes:
    """Derive ``length`` bytes via HKDF-SHA256 (independent reference backend)."""
    _validate_length(length)
    return PyCryptoHKDF(
        master=ikm, key_len=length, salt=salt, hashmod=SHA256, context=info
    )
