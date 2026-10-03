#!/usr/bin/env python3
"""Run a bundled fixture through the phasing service without a server.

Usage: python3 scripts/run_fixture.py [basic_clean|error_reads|ambiguous|disconnected]
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.config import load_settings
from app.models import PhaseRequest
from app.provenance import ProvenanceStore
from app.service import PhasingFailure, PhasingService


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "basic_clean"
    fixture_path = REPO_ROOT / "fixtures" / f"{name}.json"
    if not fixture_path.is_file():
        print(f"unknown fixture {name!r}; expected one of "
              f"{sorted(p.stem for p in (REPO_ROOT / 'fixtures').glob('*.json'))}")
        return 2

    settings = load_settings()
    service = PhasingService(settings, ProvenanceStore(settings.provenance_db_path))
    request = PhaseRequest(**json.loads(fixture_path.read_text(encoding="utf-8")))
    try:
        response = service.run_phase(request)
    except PhasingFailure as failure:
        print(json.dumps({
            "request_id": failure.request_id,
            "status": "failed",
            "error": failure.error.to_dict(),
        }, indent=2))
        return 1
    print(json.dumps(response, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
