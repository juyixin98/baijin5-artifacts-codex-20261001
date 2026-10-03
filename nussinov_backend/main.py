"""ASGI entrypoint: ``uvicorn nussinov_backend.main:app``."""
from __future__ import annotations

from .api.app import create_app

app = create_app()
