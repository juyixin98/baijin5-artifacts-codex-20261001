"""Unambiguous TLV protocol encoding for derivation labels.

Labels are NEVER concatenated as bare strings. Every field is encoded as

    u16be(len(tag)) || tag || u32be(len(value)) || value

and a derivation-info block is

    MAGIC || u16be(field_count) || field*

This makes the encoding injective: distinct field lists always produce
distinct byte strings. The classic collision ("ab"+"c" == "a"+"bc") is
impossible here, and tests assert that counterexample explicitly.

This module performs no cryptography and keeps no state.
"""

from __future__ import annotations

import struct

from .errors import InputValidationError

MAGIC = b"KDT1"

MAX_TAG_LEN = 64
MAX_VALUE_LEN = 4096
MAX_FIELDS = 32

_U16 = struct.Struct(">H")
_U32 = struct.Struct(">I")


def _check_tag(tag: bytes) -> None:
    if not isinstance(tag, (bytes, bytearray)):
        raise InputValidationError("tag must be bytes", details={"type": type(tag).__name__})
    if not 1 <= len(tag) <= MAX_TAG_LEN:
        raise InputValidationError(
            "tag length out of range",
            details={"length": len(tag), "max": MAX_TAG_LEN},
        )


def _check_value(value: bytes) -> None:
    if not isinstance(value, (bytes, bytearray)):
        raise InputValidationError(
            "value must be bytes", details={"type": type(value).__name__}
        )
    if len(value) > MAX_VALUE_LEN:
        raise InputValidationError(
            "value length out of range",
            details={"length": len(value), "max": MAX_VALUE_LEN},
        )


def encode_field(tag: bytes, value: bytes) -> bytes:
    """Encode one (tag, value) pair with explicit lengths."""
    tag = bytes(tag)
    value = bytes(value)
    _check_tag(tag)
    _check_value(value)
    return _U16.pack(len(tag)) + tag + _U32.pack(len(value)) + value


def encode_info(fields: list[tuple[bytes, bytes]]) -> bytes:
    """Encode an ordered list of (tag, value) pairs into an info block."""
    if len(fields) > MAX_FIELDS:
        raise InputValidationError(
            "too many fields", details={"count": len(fields), "max": MAX_FIELDS}
        )
    body = b"".join(encode_field(tag, value) for tag, value in fields)
    return MAGIC + _U16.pack(len(fields)) + body


def decode_info(data: bytes) -> list[tuple[bytes, bytes]]:
    """Strictly parse an info block. Trailing or truncated data is an error."""
    if not isinstance(data, (bytes, bytearray)):
        raise InputValidationError("info block must be bytes")
    data = bytes(data)
    if len(data) < len(MAGIC) + 2 or not data.startswith(MAGIC):
        raise InputValidationError("bad magic in info block")
    (count,) = _U16.unpack_from(data, len(MAGIC))
    if count > MAX_FIELDS:
        raise InputValidationError("field count exceeds limit", details={"count": count})
    offset = len(MAGIC) + 2
    fields: list[tuple[bytes, bytes]] = []
    for _ in range(count):
        if offset + 2 > len(data):
            raise InputValidationError("truncated tag length")
        (tag_len,) = _U16.unpack_from(data, offset)
        offset += 2
        if tag_len < 1 or tag_len > MAX_TAG_LEN:
            raise InputValidationError("tag length out of range", details={"length": tag_len})
        if offset + tag_len + 4 > len(data):
            raise InputValidationError("truncated tag or value length")
        tag = data[offset : offset + tag_len]
        offset += tag_len
        (value_len,) = _U32.unpack_from(data, offset)
        offset += 4
        if value_len > MAX_VALUE_LEN:
            raise InputValidationError(
                "value length out of range", details={"length": value_len}
            )
        if offset + value_len > len(data):
            raise InputValidationError("truncated value")
        value = data[offset : offset + value_len]
        offset += value_len
        fields.append((tag, value))
    if offset != len(data):
        raise InputValidationError(
            "trailing bytes after info block",
            details={"trailing": len(data) - offset},
        )
    return fields
