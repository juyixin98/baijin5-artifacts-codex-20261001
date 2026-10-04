"""Generate deterministic, committed test fixtures (out-of-band oracle data).

Run once; outputs are committed under ``fixtures/`` so tests never depend on
answers produced by the code under test at runtime.  The generator uses only
the protocol framing and AEAD primitives - never the service state machine.

    python scripts/generate_fixtures.py

It writes:

* fixtures/test-keys.json      - fixed test-only key bundle
* fixtures/vectors.json        - sealed frames + plaintext digests for several
                                 synthetic messages and chunk sizes
* fixtures/gcm-kat.json        - raw AES-256-GCM known-answer check values
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.crypto import CryptographyBackend, PyCryptodomeBackend  # noqa: E402
from app.core.keyring import KeyBundle  # noqa: E402
from app.core.protocol import (  # noqa: E402
    TAG_LEN, decode_frame, derive_nonce, encode_aad, encode_frame,
)
from app.core.sender import encode_message  # noqa: E402

FIXDIR = ROOT / "fixtures"

# Fixed, reproducible test key material (TEST FIXTURES ONLY - never prod keys).
FIXED_AEAD_KEY = bytes.fromhex(
    "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
FIXED_NONCE_KEY = bytes.fromhex(
    "1f1e1d1c1b1a191817161514131211100f0e0d0c0b0a09080706050403020100")


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def synthetic_message(seed: str, length: int) -> bytes:
    """Deterministic synthetic plaintext (not random, not external data)."""
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hashlib.sha256(
            f"{seed}:{counter}".encode()).digest())
        counter += 1
    return bytes(out[:length])


def main() -> None:
    FIXDIR.mkdir(exist_ok=True)
    bundle = KeyBundle(key_id="kat-fixed-key",
                       aead_key=FIXED_AEAD_KEY, nonce_key=FIXED_NONCE_KEY)

    (FIXDIR / "test-keys.json").write_text(json.dumps({
        "key_id": bundle.key_id,
        "aead_key_b64": b64(bundle.aead_key),
        "nonce_key_b64": b64(bundle.nonce_key),
        "warning": "test fixture key only - do not use in production",
    }, indent=2))

    backend = CryptographyBackend()
    alt = PyCryptodomeBackend()

    cases = [
        ("empty", b"", 16),
        ("short", b"hello segmented aead", 7),
        ("exact-one-chunk", b"x" * 16, 16),
        ("multi-chunk", synthetic_message("alpha", 100), 16),
        ("non-divisible", synthetic_message("beta", 100), 30),
        ("larger", synthetic_message("gamma", 5000), 1024),
    ]

    vectors: dict[str, object] = {"cases": []}
    for name, plaintext, chunk in cases:
        msg = encode_message(bundle, backend, f"msg-{name}", plaintext, chunk)
        frames = [b64(f) for f in msg.frames]
        # Every frame must open identically with the independent backend.
        cross_ok = []
        for raw in msg.frames:
            fr = decode_frame(raw)
            nonce = derive_nonce(bundle.nonce_key, fr.message_id, fr.seqno,
                                 fr.is_final)
            aad = encode_aad(fr.message_id, fr.seqno, fr.total_segments,
                             fr.is_final, fr.total_len, fr.plaintext_len)
            piece = alt.open(bundle.aead_key, nonce, fr.ciphertext, aad)
            cross_ok.append(b64(piece))
        vectors["cases"].append({
            "name": name,
            "message_id": msg.message_id,
            "chunk_size": chunk,
            "total_segments": msg.total_segments,
            "total_len": msg.total_len,
            "plaintext_b64": b64(plaintext),
            "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
            "frames_b64": frames,
            "opened_with_other_backend_b64": cross_ok,
        })

    # Raw AES-256-GCM known answer: fixed key/nonce/aad/PT, ciphertext+tag
    # recorded; both backends must reproduce it byte-for-byte.
    kat_key = bytes.fromhex("fe" * 32)
    kat_nonce = bytes.fromhex("00" * 12)
    kat_aad = b"SSEA1-KAT"
    kat_pt = b"known answer plaintext block"
    ct1 = CryptographyBackend().seal(kat_key, kat_nonce, kat_pt, kat_aad)
    ct2 = PyCryptodomeBackend().seal(kat_key, kat_nonce, kat_pt, kat_aad)
    assert ct1 == ct2, "backends disagree on fixed-input GCM seal"
    kat = {
        "key_b64": b64(kat_key),
        "nonce_b64": b64(kat_nonce),
        "aad_b64": b64(kat_aad),
        "plaintext_b64": b64(kat_pt),
        "ciphertext_tag_b64": b64(ct1),
        "tag_len": TAG_LEN,
        "ciphertext_sha256": hashlib.sha256(ct1).hexdigest(),
    }
    (FIXDIR / "gcm-kat.json").write_text(json.dumps(kat, indent=2))
    (FIXDIR / "vectors.json").write_text(json.dumps(vectors, indent=2))
    print(f"wrote fixtures to {FIXDIR}")


if __name__ == "__main__":
    main()
