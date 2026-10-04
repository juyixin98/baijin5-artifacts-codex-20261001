"""Independent end-to-end verification driver.

Starts the service (uvicorn subprocess, fresh temp database), then checks its
answers against the stdlib plaintext reference in verification/reference.py.
Exit code 0 only if every check passes.

    .venv/bin/python verification/run_verification.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from verification import reference  # noqa: E402

PORT = 8377


def main() -> int:
    fixtures = json.loads((ROOT / "verification/fixtures.json").read_text())
    base_cfg = json.loads((ROOT / "config/settings.verify.json").read_text())
    with tempfile.TemporaryDirectory() as tmp:
        base_cfg["database_path"] = str(Path(tmp) / "verify.db")
        base_cfg["audit_log_path"] = str(Path(tmp) / "audit.log")
        cfg_path = Path(tmp) / "settings.json"
        cfg_path.write_text(json.dumps(base_cfg))
        env = {**os.environ, "SENSITIVE_LAYER_CONFIG": str(cfg_path)}
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn",
             "sensitive_layer.api.main:app", "--port", str(PORT)],
            cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            client = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=10)
            for _ in range(50):
                try:
                    if client.get("/health").status_code == 200:
                        break
                except httpx.TransportError:
                    time.sleep(0.2)
            else:
                print("FAIL server did not start")
                return 1
            report = reference.run_verification(client, fixtures)
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    for c in report["checks"]:
        line = f"{'PASS' if c['ok'] else 'FAIL'} {c['name']}"
        if not c["ok"] and c["detail"]:
            line += f"  -- {c['detail']}"
        print(line)
    print(f"\n{report['passed']} passed, {report['failed']} failed")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
