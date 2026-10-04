#!/usr/bin/env python3
"""End-to-end demo against a locally running server (scripts/run_dev.sh).

Creates a round with short deadlines, commits three synthetic participants,
reveals all of them, finalizes, then verifies the public evidence offline.
Everything is synthetic; no external accounts or data are involved.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone

import httpx

from commit_reveal.crypto.commitment import compute_commitment
from commit_reveal.verify.independent import verify_evidence

BASE = "http://127.0.0.1:8529"
PARTICIPANTS = ["alice", "bob", "carol"]


def synth(name: str, kind: str) -> str:
    return hashlib.sha256(f"demo:{kind}:{name}".encode()).hexdigest()


def show(step: str, resp: httpx.Response) -> dict:
    body = resp.json()
    print(f"== {step}: HTTP {resp.status_code} "
          f"(request_id={resp.headers.get('X-Request-ID', '-')[:12]})")
    print(json.dumps(body, indent=2)[:600])
    resp.raise_for_status()
    return body


def main() -> None:
    client = httpx.Client(base_url=BASE, timeout=10)
    now = datetime.now(timezone.utc)
    commit_deadline = now + timedelta(seconds=3)
    reveal_deadline = now + timedelta(seconds=6)

    rnd = show("create round", client.post("/rounds", json={
        "participants": PARTICIPANTS,
        "commit_deadline": commit_deadline.isoformat(),
        "reveal_deadline": reveal_deadline.isoformat(),
        "min_reveals": 2,
    }))["round"]
    rid = rnd["round_id"]

    for name in PARTICIPANTS:
        commitment = compute_commitment(
            rid, name, bytes.fromhex(synth(name, "value")),
            bytes.fromhex(synth(name, "salt")),
        )
        show(f"commit {name}", client.post(f"/rounds/{rid}/commitments", json={
            "participant_id": name, "commitment": commitment,
        }))

    time.sleep(max(0.0, (commit_deadline - datetime.now(timezone.utc)).total_seconds()))
    for name in PARTICIPANTS:
        show(f"reveal {name}", client.post(f"/rounds/{rid}/reveals", json={
            "participant_id": name,
            "value": synth(name, "value"),
            "salt": synth(name, "salt"),
        }))

    time.sleep(max(0.0, (reveal_deadline - datetime.now(timezone.utc)).total_seconds()))
    result = show("finalize", client.post(f"/rounds/{rid}/finalize"))["result"]
    print(f"\nWinner: {result['winner']}\nSeed:   {result['seed_hex']}")

    evidence = client.get(f"/rounds/{rid}/evidence").json()["evidence"]
    checks = verify_evidence(evidence)
    for check in checks:
        print(f"[{'PASS' if check.passed else 'FAIL'}] {check.name}")
    assert all(c.passed for c in checks), "offline verification failed"
    print("Offline verification: all checks passed.")


if __name__ == "__main__":
    main()
