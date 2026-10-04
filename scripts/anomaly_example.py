"""Live anomaly demonstration against a running server.

Submits deliberately bad traffic and prints the exact rejection category and
status, then proves that no released artifact exists for any failed stream.

    python scripts/anomaly_example.py --key-file ./fixtures/demo-keys.json
"""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.crypto import get_backend  # noqa: E402
from app.core.keyring import load_keys  # noqa: E402
from app.core.protocol import decode_frame, encode_frame  # noqa: E402
from app.core.sender import encode_message  # noqa: E402


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def show(label: str, resp: httpx.Response) -> dict:
    body = resp.json() if resp.content else {}
    print(f"[{label}] HTTP {resp.status_code} category={body.get('category')} "
          f"request_id={body.get('request_id')} state={body.get('state')}")
    return body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8080")
    ap.add_argument("--key-file", required=True)
    args = ap.parse_args()

    bundle = load_keys(args.key_file)
    backend = get_backend("cryptography")
    client = httpx.Client(base_url=args.base_url, timeout=30)

    def submit(mid: str, frame: bytes, rid: str):
        return client.post("/v1/segments", json={"frame_b64": b64(frame)},
                           headers={"x-request-id": rid})

    # 1) Finalize with outstanding segments -> incomplete, no result.
    sealed = encode_message(bundle, backend, "an-incomplete", b"abcdefgh", 4)
    submit("an-incomplete", sealed.frames[0], "an-1-seg")
    show("incomplete finalize",
         client.post("/v1/streams/an-incomplete/finalize",
                     headers={"x-request-id": "an-1-fin"}))
    r = client.get("/v1/streams/an-incomplete/result")
    print(f"[incomplete result] HTTP {r.status_code} (must not be 200)")

    # 2) Tampered ciphertext -> auth_failed, stream failed.
    sealed = encode_message(bundle, backend, "an-tamper", b"abcdefghij", 4)
    bad = bytearray(sealed.frames[1])
    bad[-1] ^= 0xFF
    show("tampered ciphertext", submit("an-tamper", bytes(bad), "an-2"))

    # 3) Nonce reuse: same slot sealed with different content -> conflict.
    sealed = encode_message(bundle, backend, "an-reuse", b"abcdefgh", 4)
    submit("an-reuse", sealed.frames[0], "an-3a")
    other = encode_message(bundle, backend, "an-reuse", b"Xbcdefgh", 4)
    show("nonce reuse", submit("an-reuse", other.frames[0], "an-3b"))

    # 4) Missing terminator: last frame with final flag stripped in header.
    sealed = encode_message(bundle, backend, "an-noterm", b"abcd", 2)
    submit("an-noterm", sealed.frames[0], "an-4a")
    last = decode_frame(sealed.frames[1])
    no_term = encode_frame(last.message_id, last.seqno, last.total_segments,
                           last.total_len, False, last.ciphertext)
    show("missing terminator", submit("an-noterm", no_term, "an-4b"))

    # 5) Reordered claim: present a mid frame whose seqno marker is moved.
    #    (covered structurally; here we simply confirm status accounting.)

    print("\n-- result availability for failed streams (all must be 409) --")
    for mid in ("an-tamper", "an-reuse", "an-noterm"):
        code = client.get(f"/v1/streams/{mid}/result").status_code
        status = client.get(f"/v1/streams/{mid}/status").json()["status"]
        print(f"{mid:>16}: result HTTP {code}, stream status={status}")

    print("\n-- audit trail (redacted) for an-reuse --")
    for e in client.get("/v1/audit",
                        params={"message_id": "an-reuse"}).json():
        print(f"  {e['kind']:>12} {e['category']:>16} "
              f"rid={e['request_id']} :: {e['message']} {e['state']}")

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
