#!/usr/bin/env python3
"""Start the miniseed FastAPI service locally.

Usage:
    python scripts/serve.py [--host 127.0.0.1] [--port 8000]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import uvicorn  # noqa: E402

from miniseed.api import create_app  # noqa: E402
from miniseed.config import Settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run miniseed service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    settings = Settings.from_env()
    app = create_app(settings)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
