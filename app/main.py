"""Local development entry point: ``python -m app.main``."""
from __future__ import annotations

import uvicorn

from app.config import settings


def main() -> None:
    uvicorn.run(
        "app.api:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    print(f"starting {settings.service_name} v{settings.version}")
    main()
