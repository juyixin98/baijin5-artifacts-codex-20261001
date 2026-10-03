#!/usr/bin/env python3
"""Local demo: build an index from a synthetic reference and query it.

Runs the FastAPI app in-process (TestClient), plants reads at known
positions, and checks whether the top candidate recovers each planted
location. Every step is written to ``logs/demo_<run_id>.jsonl`` with the
run identity, versions, and the judgment basis of each PASS/FAIL.

Exit code is 0 only if every expectation holds; failures and unexpected
errors are reported, never swallowed.

Usage: python scripts/demo.py
"""

from __future__ import annotations

import json
import random
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.provenance import environment_versions
from app.service import create_app

RUN_ID = uuid.uuid4().hex[:12]
LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / f"demo_{RUN_ID}.jsonl"
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "demo"

K, WINDOW = 7, 4
REF_LEN = 600
READ_START, READ_LEN = 240, 90


def log(event: str, **fields) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "run_id": RUN_ID,
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def revcomp(seq: str) -> str:
    comp = {"A": "T", "C": "G", "G": "C", "T": "A"}
    return "".join(comp[b] for b in reversed(seq))


def main() -> int:
    versions = environment_versions()
    log("demo_start", versions=versions, params={"k": K, "window": WINDOW})
    print(f"demo run {RUN_ID}  (python {versions['python']}, numpy {versions['numpy']})")

    rng = random.Random(20261003)
    reference = "".join(rng.choice("ACGT") for _ in range(REF_LEN))
    planted = reference[READ_START : READ_START + READ_LEN]
    mutated = list(planted)
    for pos in (10, 45, 80):
        mutated[pos] = rng.choice([b for b in "ACGT" if b != mutated[pos]])
    mutated = "".join(mutated)

    client = TestClient(create_app(data_dir=DATA_DIR))
    checks: list[tuple[str, bool, str]] = []

    resp = client.post(
        "/indexes",
        json={
            "sequences": [{"name": "ref", "sequence": reference}],
            "config": {"k": K, "window": WINDOW},
        },
    )
    assert resp.status_code == 201, resp.text
    index_id = resp.json()["index_id"]
    log("build", index_id=index_id, stats=resp.json()["stats"])
    print(f"built index {index_id}: {resp.json()['stats']}")

    def query(name: str, read: str) -> dict:
        resp = client.post(f"/indexes/{index_id}/queries",
                           json={"name": name, "sequence": read})
        assert resp.status_code == 200, resp.text
        return resp.json()

    cases = [
        ("exact", planted, "same"),
        ("mutated(3 subs)", mutated, "same"),
        ("reverse-complement", revcomp(planted), "swapped"),
    ]
    for label, read, relation in cases:
        result = query(label, read)
        top = result["candidates"][0] if result["candidates"] else None
        ok = (
            top is not None
            and top["relation"] == relation
            and top["estimated_ref_start"] == READ_START
        )
        detail = (
            f"top={top['relation']}@{top['estimated_ref_start']} "
            f"hits={top['hits']}" if top else "no candidates"
        )
        checks.append((label, ok, detail))
        log("query_check", case=label, expected_start=READ_START,
            expected_relation=relation, result=top, passed=ok,
            basis="read planted at known offset; top candidate must recover it")

    # Negative control: an unrelated read must not produce a strong cluster.
    noise = "".join(rng.choice("ACGT") for _ in range(READ_LEN))
    result = query("noise", noise)
    weak = all(c["hits"] <= 1 for c in result["candidates"])
    checks.append(("noise read (no strong cluster)", weak,
                   f"max hits={result['candidates'][0]['hits'] if result['candidates'] else 0}"))
    log("query_check", case="noise", passed=weak,
        basis="unrelated read: random collisions only, hits per cluster <= 1")

    # Error semantics: a too-short read must be a 400, not an empty success.
    resp = client.post(f"/indexes/{index_id}/queries",
                       json={"name": "tiny", "sequence": "ACGT"})
    too_short_ok = (
        resp.status_code == 400
        and resp.json()["error"]["category"] == "SEQUENCE_TOO_SHORT"
    )
    checks.append(("too-short read -> 400 SEQUENCE_TOO_SHORT", too_short_ok,
                   f"status={resp.status_code}"))
    log("error_check", case="too_short", passed=too_short_ok,
        basis="reads shorter than k+window-1 are a defined failure category")

    print("\nresults:")
    all_ok = True
    for label, ok, detail in checks:
        all_ok &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {detail}")
    log("demo_finish", passed=all_ok,
        checks=[{"case": c, "passed": ok} for c, ok, _ in checks])
    print(f"\nlog: {LOG_PATH}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
