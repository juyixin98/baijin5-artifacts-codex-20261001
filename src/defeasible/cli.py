"""Tiny command-line entry point.

Usage::

    python -m defeasible.cli serve [--reload]
    python -m defeasible.cli load-fixture fixtures/birds.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Settings
from .language import Term
from .service import ReasoningService
from .storage import EvidenceStore
from .logging import RunLogger
from .engine import Engine


def _load_fixture(service: ReasoningService, path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    for case in data.get("cases", []):
        cid = case["case_id"]
        service.store.ensure_case(cid, case.get("description", ""))
        if "theory" in case:
            service.put_theory(cid, case["theory"])
        if case.get("evidence"):
            service.add_evidence(cid, case["evidence"])
        print(f"loaded case {cid!r}: "
              f"{len(case.get('theory', {}).get('rules', []))} rules, "
              f"{len(case.get('evidence', []))} evidence")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="defeasible")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve_p = sub.add_parser("serve", help="run the HTTP API")
    serve_p.add_argument("--reload", action="store_true")
    fix_p = sub.add_parser("load-fixture", help="load a JSON fixture file")
    fix_p.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    settings = Settings.from_env()

    if args.cmd == "load-fixture":
        from .errors import DefeasibleError

        store = EvidenceStore(settings.db_path)
        logger = RunLogger(settings.log_path)
        service = ReasoningService(store, Engine(settings.engine_limits()), logger)
        try:
            _load_fixture(service, args.path)
        except DefeasibleError as e:
            # e.g. a deliberately cyclic fixture: report cleanly, do not dump
            store.close()
            print(f"fixture rejected [{e.code}]: {e.message}", file=sys.stderr)
            if e.details:
                print(f"details: {e.details}", file=sys.stderr)
            return 2
        store.close()
        return 0

    import uvicorn

    uvicorn.run(
        "defeasible.api:app",
        host=settings.host,
        port=settings.port,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
