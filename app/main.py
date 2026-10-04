"""Service entry point: ``python -m app.main`` or ``uvicorn app.main:app``."""
from __future__ import annotations

import os

import uvicorn

from .api import app  # re-exported for uvicorn

__all__ = ["app"]


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("EDT_HOST", "127.0.0.1"),
        port=int(os.environ.get("EDT_PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    main()
