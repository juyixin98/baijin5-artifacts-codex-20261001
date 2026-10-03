"""Structured run logging (JSONL) for tests and demos.

Every log line carries the run id, so a failing test can be correlated
with the exact environment, input fixture hash, computed metrics and the
thresholds used for the verdict.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from pathlib import Path


def git_sha(cwd: str | None = None) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def environment_fingerprint() -> dict:
    import fastapi
    import numpy
    import scipy

    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy": numpy.__version__,
        "scipy": scipy.__version__,
        "fastapi": fastapi.__version__,
        "git_sha": git_sha(),
    }


def new_run_id() -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    sha = git_sha() or "nogit"
    return f"run-{stamp}-{sha}"


class RunLogger:
    def __init__(self, path: str | Path, run_id: str):
        self.path = Path(path)
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")

    def log(self, event: str, **fields) -> None:
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run_id": self.run_id,
            "event": event,
            **fields,
        }
        self._fh.write(json.dumps(record, default=str) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()
