"""End-to-end calling example for the Segmented AEAD Service.

The script seals a synthetic message locally (producer side), uploads the
segments over HTTP in a chosen order (default shuffled), finalises the stream
and downloads the released plaintext, asserting byte equality and printing a
diagnostic summary.  It needs a running server:

    # terminal 1
    SSEA_KEY_FILE=./fixtures/test-keys-file.json \
    SSEA_DB_PATH=./data/demo.sqlite3 python -m app

    # terminal 2
    python scripts/client_example.py --shuffle

Key files for the demo can be created with
``python -m app.core.keyring init ./fixtures/test-keys-file.json``.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import random
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.crypto import get_backend  # noqa: E402
from app.core.keyring import KeyBundle, load_keys  # noqa: E402
from app.core.sender import encode_message  # noqa: E402


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def load_fixture_keys(path: str | None = None) -> KeyBundle:
    """Load keys from a standard 0600 keyring file, else the fixture keys."""
    if path:
        return load_keys(path)
    import json
    data = json.loads((ROOT / "fixtures" / "test-keys.json").read_text())
    return KeyBundle(
        key_id=data["key_id"],
        aead_key=base64.b64decode(data["aead_key_b64"]),
        nonce_key=base64.b64decode(data["nonce_key_b64"]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8080")
    ap.add_argument("--chunk-size", type=int, default=16)
    ap.add_argument("--length", type=int, default=100)
    ap.add_argument("--shuffle", action="store_true",
                    help="upload segments out of order")
    ap.add_argument("--message-id", default="demo-0001")
    ap.add_argument("--key-file", default=None,
                    help="standard 0600 keyring file (defaults to fixture keys)")
    args = ap.parse_args()

    bundle = load_fixture_keys(args.key_file)
    backend = get_backend("cryptography")

    seed_plaintext = bytes(
        (i * 7 + 3) & 0xFF for i in range(args.length))
    sealed = encode_message(bundle, backend, args.message_id,
                            seed_plaintext, args.chunk_size)
    print(f"sealed {len(seed_plaintext)} bytes into "
          f"{sealed.total_segments} segments")

    order = list(range(sealed.total_segments))
    if args.shuffle:
        random.Random(20260927).shuffle(order)
        print(f"upload order: {order}")

    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        for seqno in order:
            resp = client.post(
                "/v1/segments",
                json={"frame_b64": b64(sealed.frames[seqno])},
                headers={"x-request-id": f"demo-seg-{seqno}"})
            resp.raise_for_status()
            body = resp.json()
            print(f"  segment {seqno:>3}: accepted={body['accepted']} "
                  f"received={body['received']}/{body['total_segments']} "
                  f"complete={body['complete']} "
                  f"rid={resp.headers.get('x-request-id')}")

        fin = client.post(f"/v1/streams/{args.message_id}/finalize",
                          headers={"x-request-id": "demo-final"})
        fin.raise_for_status()
        print(f"finalize: {fin.json()}")

        result = client.get(f"/v1/streams/{args.message_id}/result")
        result.raise_for_status()
        got = result.content

    ok = got == seed_plaintext
    print("released sha256:", hashlib.sha256(got).hexdigest())
    print("expected sha256:", hashlib.sha256(seed_plaintext).hexdigest())
    print("BYTE-IDENTICAL:", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
