"""Runnable service entry point.

Usage:
    python -m run                 # binds 127.0.0.1:8000
    CFIM_PORT=9000 python -m run

Configuration is environment driven (see cfim/config.py):
    CFIM_DB_PATH, CFIM_LOG_LEVEL, CFIM_DEFAULT_BUDGET,
    CFIM_MAX_ADVANCE_BUDGET, CFIM_MAX_TRANSACTIONS, ...
"""

from __future__ import annotations

import os

import uvicorn

from cfim.config import load_settings
from cfim.service import create_app

settings = load_settings()
app = create_app(settings)

if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.environ.get("CFIM_HOST", "127.0.0.1"),
        port=int(os.environ.get("CFIM_PORT", "8000")),
        log_config=None,  # keep the cfim JSON logger; uvicorn stays quiet-ish
    )
