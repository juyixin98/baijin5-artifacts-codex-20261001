"""Structured key identity.

A key is identified by the tuple ``(tenant, purpose, version, context)``.
The ``key_id`` is a digest of the unambiguous encoding of that tuple — a
human-supplied display name is metadata only and NEVER feeds the identity,
so two keys that share a display name cannot be confused, and renaming a
display name cannot silently retarget a key.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .encoding import encode_fields
from .errors import InputError

LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MIN_VERSION = 1
MAX_VERSION = 0xFFFFFFFF

KEY_ID_PREFIX = "kds1-"
KEY_ID_DIGEST_BYTES = 16  # 128 bits of SHA-256 over the canonical identity


def validate_label(value: str, *, field: str) -> str:
    if not isinstance(value, str):
        raise InputError(f"{field} must be a string")
    if not LABEL_RE.match(value):
        raise InputError(
            f"{field} must match {LABEL_RE.pattern}",
            detail=f"got {value!r}",
        )
    return value


def validate_version(version: int) -> int:
    if not isinstance(version, int) or isinstance(version, bool):
        raise InputError("version must be an integer")
    if not MIN_VERSION <= version <= MAX_VERSION:
        raise InputError(
            f"version must be in [{MIN_VERSION}, {MAX_VERSION}]",
            detail=f"got {version}",
        )
    return version


@dataclass(frozen=True)
class KeyIdentity:
    tenant: str
    purpose: str
    version: int
    context: str

    def __post_init__(self) -> None:
        validate_label(self.tenant, field="tenant")
        validate_label(self.purpose, field="purpose")
        validate_version(self.version)
        validate_label(self.context, field="context")

    def canonical(self) -> bytes:
        """Unambiguous byte encoding of this identity tuple."""
        return encode_fields(
            "kds1", "identity", self.tenant, self.purpose, self.version, self.context
        )

    @property
    def key_id(self) -> str:
        digest = hashlib.sha256(self.canonical()).digest()
        return KEY_ID_PREFIX + digest[:KEY_ID_DIGEST_BYTES].hex()
