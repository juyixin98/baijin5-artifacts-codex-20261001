"""End-to-end demo client against a running service.

Usage:
    export SAE_MASTER_KEY=$(python -c "import os; print(os.urandom(32).hex())")
    python -m sae &                                  # start the service
    python examples/client_demo.py                   # run this demo

It encrypts a message in chunks (submitted out of order), shows that a
tampered stream is rejected with a failure category, and prints the
released plaintext hash of the good stream.
"""

from __future__ import annotations

import base64
import hashlib
import os
import uuid

import httpx

BASE = os.environ.get("SAE_URL", "http://127.0.0.1:8000")


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def main() -> None:
    client = httpx.Client(base_url=BASE, timeout=10)
    rid = uuid.uuid4().hex[:8]

    # 1. Allocate a message stream.
    mid = client.post("/v1/messages", json={},
                      headers={"X-Request-ID": f"demo-{rid}-create"}).json()["message_id"]
    print(f"[demo] message_id = {mid}")

    # 2. Submit chunks OUT OF ORDER (staging tolerates reordering).
    payload = ("segmented authenticated encryption demo " * 200).encode()
    chunks = [payload[i:i + 4096] for i in range(0, len(payload), 4096)]
    for seq in reversed(range(len(chunks))):
        r = client.post(
            f"/v1/messages/{mid}/chunks",
            json={"seq": seq, "final": seq == len(chunks) - 1,
                  "plaintext_b64": b64(chunks[seq])},
            headers={"X-Request-ID": f"demo-{rid}-chunk-{seq}"},
        )
        r.raise_for_status()
    print(f"[demo] staged {len(chunks)} chunks (out of order)")

    # 3. Plaintext is refused before finalization.
    r = client.get(f"/v1/messages/{mid}/plaintext")
    print(f"[demo] premature plaintext read -> {r.status_code} "
          f"{r.json()['error']['category']}")

    # 4. Finalize: whole stream authenticated, then released.
    r = client.post(f"/v1/messages/{mid}/finalize",
                    headers={"X-Request-ID": f"demo-{rid}-fin"})
    print(f"[demo] finalize -> {r.json()}")

    # 5. Read the released plaintext and verify byte-identity.
    got = base64.b64decode(client.get(f"/v1/messages/{mid}/plaintext")
                           .json()["plaintext_b64"])
    assert got == payload
    print(f"[demo] released {len(got)} bytes, sha256={hashlib.sha256(got).hexdigest()}")
    print("[demo] plaintext byte-identical to original: OK")

    # 6. A truncated stream (no final chunk) is rejected with a category.
    mid2 = client.post("/v1/messages", json={}).json()["message_id"]
    client.post(f"/v1/messages/{mid2}/chunks",
                json={"seq": 0, "final": False, "plaintext_b64": b64(b"partial")})
    r = client.post(f"/v1/messages/{mid2}/finalize")
    print(f"[demo] truncated stream finalize -> {r.status_code} "
          f"{r.json()['error']['category']}")

    # 7. Audit trail shows the decisions (hashes only, no plaintext).
    events = client.get(f"/v1/messages/{mid}/audit").json()["events"]
    for e in events:
        print(f"[audit] req={e['request_id']:<22} {e['event']:<18} {e['decision']}")


if __name__ == "__main__":
    main()
