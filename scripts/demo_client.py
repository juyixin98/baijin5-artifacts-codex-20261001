#!/usr/bin/env python3
"""End-to-end SCRAM-SHA-256 demonstration against the local test server.

Runs the full client state machine over HTTP against
``http://127.0.0.1:8765`` and prints each wire step with a correlation id.

Usage:
    python scripts/demo_client.py --username alice
    SCRAM_TEST_PASSWORD=pencil python scripts/demo_client.py -u user
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scram_auth.client import ScramClientStateMachine
from scram_auth.errors import ScramError

BASE_URL_DEFAULT = "http://127.0.0.1:8765"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SCRAM-SHA-256 local demo client.")
    parser.add_argument("-u", "--username", required=True)
    parser.add_argument("--url", default=BASE_URL_DEFAULT)
    return parser.parse_args()


def explain(step: str, direction: str, payload: str, request_id: str) -> None:
    print(f"[request_id={request_id}] {direction} [{step}]")
    print(f"    {payload}")


def main() -> int:
    args = parse_args()
    password = os.environ.get("SCRAM_TEST_PASSWORD") or getpass.getpass("password: ")
    request_id = "demo-" + uuid.uuid4().hex[:16]
    client = ScramClientStateMachine(args.username, password)

    with httpx.Client(base_url=args.url, timeout=10.0) as http:
        health = http.get("/healthz")
        print("server health:", json.dumps(health.json(), ensure_ascii=False))

        # Step 1: client-first-message.
        first = client.client_first_message()
        explain("client-first", "C -> S", first, request_id)
        r1 = http.post(
            "/v1/auth/scram/first",
            json={"message": first},
            headers={"X-Request-ID": request_id},
        )
        if r1.status_code != 200:
            print("server-first FAILED:", r1.status_code, r1.text)
            return 1
        body1 = r1.json()
        explain("server-first", "S -> C", body1["server_first"], request_id)

        # Step 2: client-final-message (proof).
        final = client.handle_server_first(body1["server_first"])
        explain("client-final (proof)", "C -> S", final, request_id)
        r2 = http.post(
            "/v1/auth/scram/final",
            json={"session_id": body1["session_id"], "message": final},
            headers={"X-Request-ID": request_id},
        )
        if r2.status_code != 200:
            print("client-final FAILED:", r2.status_code, r2.text)
            return 1
        body2 = r2.json()
        explain("server-final (verifier)", "S -> C", body2["server_final"], request_id)

        # Step 3: verify server signature (mutual authentication).
        client.handle_server_final(body2["server_final"])

    print(f"\nMUTUAL AUTHENTICATION COMPLETE: client phase = {client.phase.value}")
    print("server confirmed identity:", body2.get("username"))
    print("correlation id:", request_id, "(see logs/audit.jsonl)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ScramError as exc:
        print(f"SCRAM failure [{exc.category.value}]: {exc.message}", file=sys.stderr)
        if exc.detail:
            print("detail:", json.dumps(exc.detail, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
