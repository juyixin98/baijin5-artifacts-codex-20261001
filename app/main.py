"""Runnable service entrypoint: ``python -m app.main`` or ``uvicorn app.main:app``."""

from __future__ import annotations

from .api import create_app
from .config import get_settings
from .logging_setup import configure_logging
from .repository import Repository
from .service import FimService


def build_app():
    settings = get_settings()
    configure_logging(settings.log_level)
    repository = Repository(settings.database_path)
    service = FimService(repository, settings)
    return create_app(service, log_level=settings.log_level)


app = build_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level=settings.log_level.lower(),
    )
