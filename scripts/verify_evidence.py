#!/usr/bin/env python3
"""Offline evidence verifier.

Usage: python scripts/verify_evidence.py evidence.json

Recomputes commitments, seed, and draw from the public evidence bundle and
prints each check. Exits non-zero if any check fails.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commit_reveal.verify.verifier import verify_evidence


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    evidence = json.loads(Path(sys.argv[1]).read_text())
    report = verify_evidence(evidence)
    for check in report.checks:
        mark = "PASS" if check.ok else "FAIL"
        print(f"[{mark}] {check.name}: {check.detail}")
    print(f"overall: {'VALID' if report.valid else 'INVALID'}")
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
