"""Load a generated JSONL sample file into a running service and run estimation.

Uses only the Python standard library. Assumes the server is running
(scripts/run_server.sh).

Usage:
    python scripts/load_sample.py data/sample_balanced.jsonl
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE_URL = "http://127.0.0.1:8000"


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return {"status": resp.status, "json": json.loads(resp.read())}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "json": json.loads(exc.read())}


def main(path: str) -> int:
    p = Path(path)
    records = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    experiment_id = p.stem

    # Declarations are derived from the first record; sample files only carry
    # pre-treatment covariates (the leakage file is intentionally not loaded
    # here - register it manually with pre_treatment=False to see rejection).
    cov_names = sorted(records[0]["covariates"])
    declarations = [{"name": name, "pre_treatment": True} for name in cov_names]

    r = _post("/experiments", {
        "experiment_id": experiment_id,
        "description": f"loaded from {p.name}",
        "covariates": declarations,
    })
    print("register:", r["status"], r["json"])
    if r["status"] not in (201,):
        return 1

    r = _post(f"/experiments/{experiment_id}/observations", {"observations": records})
    print("upload:", r["status"], r["json"])

    r = _post(f"/experiments/{experiment_id}/runs", {"theta_source": "control"})
    print("run:", r["status"])
    print(json.dumps(r["json"], indent=2)[:4000])
    return 0 if r["status"] == 201 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "data/sample_balanced.jsonl"))
