"""Unambiguous label encoding (protocol encoding boundary).

Labels are NEVER turned into bytes by plain string concatenation. Each field
is encoded as a typed, length-prefixed record::

    MAGIC  := b"KDS1"                       (4 bytes, domain marker)
    blob   := MAGIC || u32(count) || record * count
    record := u8(type) || u32(length) || payload
    type 0x01: payload is UTF-8 text
    type 0x02: payload is a u32 big-endian integer (length fixed to 4)

The encoding is injective: distinct field tuples always map to distinct byte
strings. The classic counterexample — ("ab", "c") vs ("a", "bc") — collides
under naive concatenation but not here, because lengths are explicit.

Decoding is provided so tests (and only tests / tooling) can prove
round-trips and injectivity.
"""

from __future__ import annotations

import struct

from .errors import InputError

MAGIC = b"KDS1"
TYPE_STR = 0x01
TYPE_U32 = 0x02

MAX_LABEL_BYTES = 256
MAX_FIELD_COUNT = 64

_HEADER = struct.Struct(">BI")  # type tag + payload length
_COUNT = struct.Struct(">I")
_U32 = struct.Struct(">I")


def _encode_str(value: str) -> bytes:
    payload = value.encode("utf-8")
    if not payload:
        raise InputError("label fields must not be empty")
    if len(payload) > MAX_LABEL_BYTES:
        raise InputError(
            f"label field exceeds {MAX_LABEL_BYTES} encoded bytes",
            detail=f"got {len(payload)} bytes",
        )
    return _HEADER.pack(TYPE_STR, len(payload)) + payload


def _encode_u32(value: int) -> bytes:
    if not 0 <= value <= 0xFFFFFFFF:
        raise InputError(
            "integer label fields must fit in u32",
            detail=f"got {value!r}",
        )
    return _HEADER.pack(TYPE_U32, _U32.size) + _U32.pack(value)


def encode_fields(*fields: str | int) -> bytes:
    """Encode a field sequence into an unambiguous byte string."""
    if not fields:
        raise InputError("at least one label field is required")
    if len(fields) > MAX_FIELD_COUNT:
        raise InputError(
            f"too many label fields (max {MAX_FIELD_COUNT})",
            detail=f"got {len(fields)}",
        )
    records = []
    for field in fields:
        if isinstance(field, str):
            records.append(_encode_str(field))
        elif isinstance(field, int) and not isinstance(field, bool):
            records.append(_encode_u32(field))
        else:
            raise InputError(
                "label fields must be str or int",
                detail=f"got {type(field).__name__}",
            )
    return MAGIC + _COUNT.pack(len(records)) + b"".join(records)


def decode_fields(blob: bytes) -> tuple[str | int, ...]:
    """Inverse of :func:`encode_fields`; raises InputError on malformed input."""
    if not blob.startswith(MAGIC):
        raise InputError("encoded label blob has a bad magic prefix")
    offset = len(MAGIC)
    if len(blob) < offset + _COUNT.size:
        raise InputError("encoded label blob is truncated (field count)")
    (count,) = _COUNT.unpack_from(blob, offset)
    offset += _COUNT.size
    if count == 0 or count > MAX_FIELD_COUNT:
        raise InputError(
            "encoded label blob has an implausible field count",
            detail=f"count={count}",
        )
    fields: list[str | int] = []
    for _ in range(count):
        if len(blob) < offset + _HEADER.size:
            raise InputError("encoded label blob is truncated (record header)")
        type_tag, length = _HEADER.unpack_from(blob, offset)
        offset += _HEADER.size
        if len(blob) < offset + length:
            raise InputError("encoded label blob is truncated (payload)")
        payload = blob[offset : offset + length]
        offset += length
        if type_tag == TYPE_STR:
            fields.append(payload.decode("utf-8"))
        elif type_tag == TYPE_U32:
            if length != _U32.size:
                raise InputError("u32 label field has a bad payload length")
            fields.append(_U32.unpack(payload)[0])
        else:
            raise InputError(
                "encoded label blob has an unknown type tag",
                detail=f"tag=0x{type_tag:02x}",
            )
    if offset != len(blob):
        raise InputError("encoded label blob has trailing bytes")
    return tuple(fields)
