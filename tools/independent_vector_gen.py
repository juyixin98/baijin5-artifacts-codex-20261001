#!/usr/bin/env python3
"""Independent golden-vector generator for the keytree derivation tree.

Independence contract: this tool does NOT import any keytree module. It
re-implements the TLV encoding and tree walk directly from the written
specification (docs/semantics.md) and computes HKDF-SHA256 exclusively via
PyCryptodome, while the service under test uses the `cryptography` backend.
The generated fixture therefore is a reference answer produced by a
different implementation stack, not by the tested core itself.

Usage: .venv/bin/python tools/independent_vector_gen.py
Writes: tests/fixtures/golden_tree_vectors.json
"""

from __future__ import annotations

import hashlib
import hmac
import json
import struct
from pathlib import Path

from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import HKDF

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# --- spec constants (copied from docs/semantics.md, not from source) ------
MAGIC = b"KDT1"
SALT = hashlib.sha256(b"keytree-local-test-salt-v1").digest()
FINGERPRINT_TAG = b"keytree-check-v1"
INTERMEDIATE_LEN = 32


def tlv(fields: list[tuple[bytes, bytes]]) -> bytes:
    body = b""
    for tag, value in fields:
        body += struct.pack(">H", len(tag)) + tag
        body += struct.pack(">I", len(value)) + value
    return MAGIC + struct.pack(">H", len(fields)) + body


def hkdf(ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(
        master=ikm, key_len=length, salt=SALT, hashmod=SHA256, context=info
    )


def derive(root: bytes, tenant: str, purpose: str, version: int,
           context: bytes, length: int) -> bytes:
    chain = hkdf(root, tlv([(b"scope", b"tenant"), (b"tenant", tenant.encode())]),
                 INTERMEDIATE_LEN)
    chain = hkdf(chain, tlv([(b"scope", b"purpose"), (b"purpose", purpose.encode())]),
                 INTERMEDIATE_LEN)
    chain = hkdf(chain, tlv([(b"scope", b"version"),
                             (b"version", struct.pack(">I", version))]),
                 INTERMEDIATE_LEN)
    return hkdf(chain, tlv([(b"scope", b"context"), (b"context", context)]), length)


def key_id(tenant: str, purpose: str, version: int, context: bytes) -> str:
    descriptor = tlv([
        (b"tenant", tenant.encode()),
        (b"purpose", purpose.encode()),
        (b"version", struct.pack(">I", version)),
        (b"context", context),
    ])
    return "kdt1_" + hashlib.sha256(descriptor).hexdigest()[:40]


def fingerprint(key: bytes) -> str:
    return hmac.new(key, FINGERPRINT_TAG, hashlib.sha256).hexdigest()[:32]


CASES = [
    # (tenant, purpose, version, context, length)
    ("acme", "encryption", 1, b"", 32),
    ("acme", "signing", 1, b"", 32),
    ("acme", "encryption", 2, b"", 32),
    ("globex", "encryption", 1, b"", 32),
    ("acme", "encryption", 1, b"order-42", 32),
    ("acme", "encryption", 1, b"", 64),
]


def main() -> None:
    root = bytes.fromhex((FIXTURES / "test_root.hex").read_text().strip())
    vectors = []
    for tenant, purpose, version, context, length in CASES:
        key = derive(root, tenant, purpose, version, context, length)
        vectors.append({
            "tenant": tenant,
            "purpose": purpose,
            "version": version,
            "context_hex": context.hex(),
            "length": length,
            "key_id": key_id(tenant, purpose, version, context),
            "key_hex": key.hex(),
            "fingerprint": fingerprint(key),
        })
    out = {
        "generator": "tools/independent_vector_gen.py (PyCryptodome only)",
        "root_fixture": "tests/fixtures/test_root.hex",
        "vectors": vectors,
    }
    target = FIXTURES / "golden_tree_vectors.json"
    target.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {target} with {len(vectors)} vectors")


if __name__ == "__main__":
    main()
