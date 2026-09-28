#!/usr/bin/env python3
"""Replay a single run id from the SQLite registry and print its evidence.

Usage:
    python scripts/replay_run.py artifacts/runs.db run-exp-iid-both_correct-0000-20260928
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aipw_backend.repository import RunRepository  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    db_path, run_id = argv[1], argv[2]
    with RunRepository(db_path) as repo:
        row = repo.get(run_id)
    if row is None:
        print(f"unknown run_id: {run_id}")
        return 1
    print(json.dumps(row, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
