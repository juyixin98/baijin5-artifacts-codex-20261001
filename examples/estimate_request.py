#!/usr/bin/env python3
"""Service call examples against a locally running server.

Usage:
    bash scripts/run_server.sh &          # or: python3 -m uvicorn app.main:app
    python3 examples/estimate_request.py  # writes examples/output/*.json
"""

from __future__ import annotations

import base64
import json
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8495"
REPO = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "output"


def post(path: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path) as resp:
        return json.loads(resp.read())


def b64_png(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    fixtures = REPO / "fixtures"

    health = get("/health")
    (OUT / "health.json").write_text(json.dumps(health, indent=2))
    print("health:", health["versions"])

    for name in ("integer_shift", "subpixel_shift", "periodic_texture", "constant_image"):
        payload = {
            "image_a": b64_png(fixtures / f"{name}_a.png"),
            "image_b": b64_png(fixtures / f"{name}_b.png"),
        }
        body = post("/v1/estimate", payload)
        (OUT / f"estimate_{name}.json").write_text(json.dumps(body, indent=2))
        print(
            f"estimate {name}: status={body['status']} shift={body['shift']} "
            f"confidence={body['confidence']} failures={body['failures']} "
            f"uncertainties={body['uncertainties']}"
        )

    payload = {
        "image_a": b64_png(fixtures / "integer_shift_a.png"),
        "image_b": b64_png(fixtures / "integer_shift_b.png"),
    }
    tiled = post("/v1/estimate/tiled", payload)
    (OUT / "estimate_tiled.json").write_text(json.dumps(tiled, indent=2))
    print("tiled:", tiled["status"], tiled["global_shift"], f"used {tiled['tiles_used']}/{tiled['tiles_total']}")

    report = post("/v1/validation/run")
    (OUT / "validation_run.json").write_text(json.dumps(report, indent=2))
    print("validation:", report["report"]["summary"])


if __name__ == "__main__":
    main()
