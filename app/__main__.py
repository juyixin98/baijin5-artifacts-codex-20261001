"""Local ASGI entrypoint: ``python -m app`` or ``uvicorn app.api.server``."""

from __future__ import annotations

import uvicorn

from .config import Settings


def main() -> None:  # pragma: no cover - process launcher
    settings = Settings.from_env()
    uvicorn.run(
        "app.api.server:create_app_from_env",
        factory=True,
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
