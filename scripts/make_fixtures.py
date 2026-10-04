"""Generate the reference test vectors in tests/fixtures/vectors.json.

The expected ciphertexts here are produced with PyCryptodome
(``sae.crypto_verify.IndependentVerifier``) - NOT with the
``cryptography``-based core adapter under test - so the suite compares
the core implementation against independently generated answers.

Run:  .venv/bin/python scripts/make_fixtures.py
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from sae import protocol
from sae.crypto_verify import IndependentVerifier

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "vectors.json"

MASTER_KEY = bytes(range(32))  # 000102...1f, test-only key


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def make_message(msg_id: bytes, salt: bytes, nonce_base: bytes,
                 chunks: list[bytes]) -> dict:
    verifier = IndependentVerifier()
    key = protocol.derive_message_key(MASTER_KEY, salt, msg_id)
    out_chunks = []
    for seq, pt in enumerate(chunks):
        final = seq == len(chunks) - 1
        aad = protocol.encode_aad(msg_id, seq, final, len(pt))
        nonce = protocol.derive_nonce(nonce_base, seq)
        ct = verifier.encrypt(key, nonce, aad, pt)
        out_chunks.append({
            "seq": seq,
            "final": final,
            "plaintext_b64": b64(pt),
            "aad_hex": aad.hex(),
            "nonce_hex": nonce.hex(),
            "ciphertext_b64": b64(ct),
        })
    return {
        "message_id": msg_id.hex(),
        "salt_hex": salt.hex(),
        "nonce_base_hex": nonce_base.hex(),
        "key_hex": key.hex(),
        "chunks": out_chunks,
    }


def main() -> None:
    vectors = {
        "description": "SAE1 reference vectors, generated with PyCryptodome",
        "master_key_hex": MASTER_KEY.hex(),
        "messages": [
            make_message(
                bytes.fromhex("aa" * 15 + "01"),
                bytes.fromhex("11" * 16),
                bytes.fromhex("22" * 8),
                [
                    b"The quick brown fox ",
                    b"jumps over the lazy ",
                    b"dog. " * 100,
                    b"",  # empty final chunk is legal
                ],
            ),
            make_message(
                bytes.fromhex("bb" * 15 + "02"),
                bytes.fromhex("33" * 16),
                bytes.fromhex("44" * 8),
                [bytes(range(256))],
            ),
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(vectors, indent=2) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
