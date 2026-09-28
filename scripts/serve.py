#!/usr/bin/env python3
"""Serve the AIPW HTTP API.

Environment variables:
    AIPW_DB_PATH      SQLite file for the run registry (default artifacts/api.db)
    AIPW_CONFIG_PATH  JSON config (default configs/default.json)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

from aipw_backend.api import create_app  # noqa: E402


def main() -> int:
    db = os.environ.get("AIPW_DB_PATH", str(ROOT / "artifacts" / "api.db"))
    cfg = os.environ.get("AIPW_CONFIG_PATH", str(ROOT / "configs" / "default.json"))
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    app = create_app(db_path=db, config_path=cfg)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("AIPW_PORT", "8000")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
