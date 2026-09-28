"""ASGI entrypoint: ``uvicorn tensorcraft.main:app``."""

from __future__ import annotations

import os

from .api import create_app
from .config import load_config

_CONFIG_PATH = os.environ.get("TENSORCRAFT_CONFIG")
app = create_app(load_config(_CONFIG_PATH))
