"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.routes import router
from app.config import Settings, load_settings
from app.logging_utils import get_logger
from app.provenance import ProvenanceStore
from app.version import APP_VERSION


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title=settings.app_name, version=APP_VERSION)
    app.state.settings = settings
    app.state.store = ProvenanceStore(settings.db_path)
    app.state.logger = get_logger()
    app.include_router(router)
    return app


app = create_app()
