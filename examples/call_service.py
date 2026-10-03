"""Minimal service-call examples against a locally running server.

Usage:
    uvicorn app.api:app --port 8000          # terminal 1
    python examples/call_service.py          # terminal 2

Covers one success path per endpoint plus the two main failure categories
(NO_LEGAL_SEAM, INVALID_IMAGE) so abnormal responses are observable too.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def post(path: str, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def get(path: str) -> tuple[int, dict]:
    with urllib.request.urlopen(BASE + path) as resp:
        return resp.status, json.loads(resp.read())


def b64(name: str) -> str:
    return base64.b64encode((FIXTURES / name).read_bytes()).decode()


def main() -> None:
    report: dict[str, object] = {}

    _, version = get("/v1/version")
    report["version"] = version["data"]["versions"]
    print("versions:", json.dumps(report["version"], indent=2))

    status, seam = post("/v1/seam", {"image_b64": b64("step_5x6.png")})
    report["seam_ok"] = {"http": status, "body": seam}
    print("\nPOST /v1/seam ->", status)
    print(json.dumps(seam, indent=2))

    status, carve = post(
        "/v1/carve", {"image_b64": b64("step_5x6.png"), "num_seams": 3}
    )
    carve["data"].pop("final_image_b64")  # keep the console output readable
    report["carve_ok"] = {"http": status, "body": carve}
    print("\nPOST /v1/carve ->", status)
    print(json.dumps(carve, indent=2))

    status, job = post(
        "/v1/jobs",
        {"image_b64": b64("step_5x6.png"), "num_seams": 3, "chunk_size": 1},
    )
    job_id = job["data"]["job_id"]
    while True:
        _, state = get(f"/v1/jobs/{job_id}")
        if state["data"]["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    report["job"] = {"http": status, "final": state}
    print("\njob final state:")
    print(json.dumps(state, indent=2))

    status, blocked = post(
        "/v1/seam",
        {"image_b64": b64("step_5x6.png"), "protect_mask_b64": b64("mask_row_5x6.png")},
    )
    report["seam_no_legal"] = {"http": status, "body": blocked}
    print("\nPOST /v1/seam (fully protected row) ->", status)
    print(json.dumps(blocked, indent=2))

    status, invalid = post("/v1/seam", {"image_b64": "!!!not-base64!!!"})
    report["seam_invalid_image"] = {"http": status, "body": invalid}
    print("\nPOST /v1/seam (invalid image) ->", status)
    print(json.dumps(invalid, indent=2))

    out = Path(__file__).resolve().parent.parent / "artifacts" / "example_run.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nfull report written to {out}")


if __name__ == "__main__":
    main()
