#!/usr/bin/env python3
"""Assemble the service from local fixtures/env and serve it over HTTP.

Usage:
    KDS_ROOT_KEY_HEX=$(cat fixtures/root_key.hex) .venv/bin/python scripts/serve.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn

from kds.api import create_app
from kds.service import DerivationTreeService
from kds.state import StateStore

DB_PATH = str(Path(__file__).resolve().parent.parent / "fixtures" / "kds.sqlite3")


def main() -> None:
    store = StateStore(DB_PATH)
    service = DerivationTreeService.from_env(store)
    uvicorn.run(create_app(service, store), host="127.0.0.1", port=8471)


if __name__ == "__main__":
    main()
