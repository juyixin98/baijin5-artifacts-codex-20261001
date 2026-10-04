"""Domain-separated, length-prefixed message framing.

The blind-index input binds the purpose and the normalization version to the
value, so the same cleartext under two purposes yields unrelated indexes and
an index can never be replayed across domains. The AAD for encryption binds
record identity and field context to the ciphertext. Length-prefixing removes
concatenation ambiguity between fields.
"""
from __future__ import annotations

_INDEX_PREFIX = b"BI\x01"
_AAD_PREFIX = b"AAD\x01"


def _lp(data: bytes) -> bytes:
    if len(data) > 0xFFFF:
        raise ValueError("framing field too long")
    return len(data).to_bytes(2, "big") + data


def index_message(purpose: str, norm_version: str, normalized_value: str) -> bytes:
    return (
        _INDEX_PREFIX
        + _lp(purpose.encode("utf-8"))
        + _lp(norm_version.encode("utf-8"))
        + _lp(normalized_value.encode("utf-8"))
    )


def aad_message(record_id: str, field: str, purpose: str) -> bytes:
    return (
        _AAD_PREFIX
        + _lp(record_id.encode("utf-8"))
        + _lp(field.encode("utf-8"))
        + _lp(purpose.encode("utf-8"))
    )
