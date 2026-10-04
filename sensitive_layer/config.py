"""Settings loading and validation.

In this local setup all key material comes from the JSON config file. That is
a deliberate dev-fixture simplification; a production deployment must source
keys from a KMS/HSM instead of a file next to the database.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .errors import ValidationError

MIN_INDEX_BITS = 8
MAX_INDEX_BITS = 256
KEY_LEN_BYTES = 32


@dataclass(frozen=True)
class Settings:
    database_path: str
    audit_log_path: str
    allow_plaintext_read: bool
    index_bits: int
    norm_version: str
    purposes: dict[str, str]                 # purpose -> field type
    seed_keys: dict[str, dict[int, bytes]]   # "enc"/"index" -> version -> key
    active_enc_version: int
    active_index_version: int


def _parse_keys(raw: dict) -> dict[str, dict[int, bytes]]:
    keys: dict[str, dict[int, bytes]] = {}
    for kind in ("enc", "index"):
        if kind not in raw:
            raise ValidationError(f"config keys.{kind} missing")
        versions: dict[int, bytes] = {}
        for ver, hexval in raw[kind].items():
            key = bytes.fromhex(hexval)
            if len(key) != KEY_LEN_BYTES:
                raise ValidationError(f"keys.{kind}.{ver} must be 32 bytes hex")
            versions[int(ver)] = key
        keys[kind] = versions
    return keys


def load_settings(path: str | Path) -> Settings:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    index_bits = int(raw["index_bits"])
    if not (MIN_INDEX_BITS <= index_bits <= MAX_INDEX_BITS):
        raise ValidationError(
            f"index_bits must be within [{MIN_INDEX_BITS}, {MAX_INDEX_BITS}]")
    seed = _parse_keys(raw["keys"])
    active_enc = int(raw["active_enc_version"])
    active_index = int(raw["active_index_version"])
    if active_enc not in seed["enc"]:
        raise ValidationError("active_enc_version has no matching key")
    if active_index not in seed["index"]:
        raise ValidationError("active_index_version has no matching key")
    return Settings(
        database_path=raw["database_path"],
        audit_log_path=raw["audit_log_path"],
        allow_plaintext_read=bool(raw.get("allow_plaintext_read", False)),
        index_bits=index_bits,
        norm_version=raw["norm_version"],
        purposes=dict(raw["purposes"]),
        seed_keys=seed,
        active_enc_version=active_enc,
        active_index_version=active_index,
    )
