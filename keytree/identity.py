"""Key identity model.

A key's identity is the tuple (tenant, purpose, version, context). The
stable key_id is a hash of the canonical TLV encoding of that tuple — never
of the display name. Display names are free-form metadata bound separately
in the store; they play no role in derivation or identity, so two keys with
the same display name remain distinct and one key may be renamed without
changing its identity.
"""

from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass

from . import encoding
from .errors import InputValidationError

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MAX_CONTEXT_LEN = 256
MAX_VERSION = 0xFFFFFFFF

KEY_ID_PREFIX = "kdt1_"
KEY_ID_HEX_LEN = 40  # 160 bits of SHA-256 — collision-resistant identity


def _validate_label(field: str, value: str) -> str:
    if not isinstance(value, str):
        raise InputValidationError(
            f"{field} must be a string", details={"type": type(value).__name__}
        )
    if not _NAME_RE.match(value):
        raise InputValidationError(
            f"{field} must match {_NAME_RE.pattern}",
            details={"field": field, "value": value},
        )
    return value


def _validate_version(version: int) -> int:
    if not isinstance(version, int) or isinstance(version, bool):
        raise InputValidationError(
            "version must be an integer", details={"type": type(version).__name__}
        )
    if not 0 <= version <= MAX_VERSION:
        raise InputValidationError(
            "version out of range",
            details={"version": version, "max": MAX_VERSION},
        )
    return version


def _validate_context(context: bytes) -> bytes:
    if not isinstance(context, (bytes, bytearray)):
        raise InputValidationError(
            "context must be bytes", details={"type": type(context).__name__}
        )
    context = bytes(context)
    if len(context) > MAX_CONTEXT_LEN:
        raise InputValidationError(
            "context too long",
            details={"length": len(context), "max": MAX_CONTEXT_LEN},
        )
    return context


@dataclass(frozen=True)
class KeyIdentity:
    """Immutable identity tuple for a derivable key."""

    tenant: str
    purpose: str
    version: int
    context: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant", _validate_label("tenant", self.tenant))
        object.__setattr__(self, "purpose", _validate_label("purpose", self.purpose))
        object.__setattr__(self, "version", _validate_version(self.version))
        object.__setattr__(self, "context", _validate_context(self.context))

    def descriptor(self) -> bytes:
        """Canonical TLV encoding of the identity; basis of key_id."""
        return encoding.encode_info(
            [
                (b"tenant", self.tenant.encode("utf-8")),
                (b"purpose", self.purpose.encode("utf-8")),
                (b"version", struct.pack(">I", self.version)),
                (b"context", self.context),
            ]
        )

    @property
    def key_id(self) -> str:
        digest = hashlib.sha256(self.descriptor()).hexdigest()
        return KEY_ID_PREFIX + digest[:KEY_ID_HEX_LEN]
