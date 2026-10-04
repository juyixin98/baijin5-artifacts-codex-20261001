"""Independent reference implementation of the protocol, stdlib only.

This module deliberately does NOT import anything from ``commit_reveal``.
It re-derives every construction from the written spec (README §Protocol)
using only ``hashlib``/``hmac``, so test expectations are anchored outside
the implementation under test. The frozen vectors in
``tests/fixtures/reference_vectors.json`` were generated with this module.
"""

from __future__ import annotations

import hashlib
import hmac

DOMAIN_COMMITMENT = b"CRP-COMMIT-v1"
DOMAIN_SEED = b"CRP-SEED-v1"
DOMAIN_SET = b"CRP-SET-v1"


def _field(data: bytes) -> bytes:
    return len(data).to_bytes(2, "big") + data


def commitment(round_id: str, participant_id: str, value: bytes, salt: bytes) -> str:
    preimage = b"".join(
        _field(p)
        for p in (
            DOMAIN_COMMITMENT,
            round_id.encode(),
            participant_id.encode(),
            value,
            salt,
        )
    )
    return hashlib.sha256(preimage).hexdigest()


def commitment_set_hash(entries: list[tuple[str, str]]) -> str:
    parts = [_field(DOMAIN_SET)]
    for pid, c in sorted(entries):
        parts.append(_field(pid.encode()))
        parts.append(_field(c.encode("ascii")))
    return hashlib.sha256(b"".join(parts)).hexdigest()


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    out = b""
    block = b""
    counter = 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def seed(round_id: str, values: list[bytes]) -> bytes:
    master = b"".join(_field(v) for v in sorted(values))
    prk = _hkdf_extract(round_id.encode(), master)
    return _hkdf_expand(prk, DOMAIN_SEED, 32)


def ranking(eligible: list[str], seed_bytes: bytes) -> list[str]:
    items = sorted(eligible)
    stream = hashlib.shake_256(seed_bytes).digest(max(1, len(items)) * 8 * 16)
    offset = 0
    for i in range(len(items) - 1, 0, -1):
        bound = i + 1
        limit = (1 << 64) - ((1 << 64) % bound)
        while True:
            candidate = int.from_bytes(stream[offset : offset + 8], "big")
            offset += 8
            if candidate < limit:
                j = candidate % bound
                break
        items[i], items[j] = items[j], items[i]
    return items
