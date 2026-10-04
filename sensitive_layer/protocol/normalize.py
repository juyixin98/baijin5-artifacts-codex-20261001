"""Canonical normalization per field type.

Normalization is part of the indexed identity of a value: two inputs that
normalize to the same canonical form produce the same blind index and confirm
as equal. The normalization version (see ``NORM_VERSION``) is mixed into the
index message, so a future change of these rules cannot silently alias with
indexes built under older rules.
"""
from __future__ import annotations

import unicodedata

from ..errors import ValidationError

NORM_VERSION = "nfkc-casefold-v1"

FIELD_TYPES = ("email", "phone", "name", "raw")


def normalize(value: str, field_type: str) -> str:
    if not isinstance(value, str):
        raise ValidationError("value must be a string or null")
    if field_type == "email":
        out = _normalize_email(value)
    elif field_type == "phone":
        out = _normalize_phone(value)
    elif field_type == "name":
        out = _normalize_name(value)
    elif field_type == "raw":
        out = unicodedata.normalize("NFKC", value)
    else:
        raise ValidationError(f"unknown field_type: {field_type!r}")
    if out == "":
        raise ValidationError("value is empty after normalization")
    return out


def _normalize_email(value: str) -> str:
    s = unicodedata.normalize("NFKC", value).strip().casefold()
    if any(ch.isspace() for ch in s):
        raise ValidationError("email contains whitespace")
    return s


def _normalize_phone(value: str) -> str:
    s = unicodedata.normalize("NFKC", value).strip()
    plus = s.startswith("+")
    digits = "".join(ch for ch in s if ch.isdigit())
    return ("+" if plus else "") + digits


def _normalize_name(value: str) -> str:
    s = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(s.split())
