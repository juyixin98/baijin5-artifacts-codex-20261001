"""Application factory and local entrypoint."""

from __future__ import annotations

from fastapi import FastAPI

from app import __version__
from app.api.deps import get_cached_settings
from app.api.routes import register_error_handlers, router
from app.config import Settings
from app.logging_config import configure_logging, get_logger


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_cached_settings()
    configure_logging(settings.log_dir, settings.log_level, settings.log_to_file)
    logger = get_logger()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Rule-based enzymatic cleavage fragment enumeration with explicit "
            "blocking context, missed cleavages, terminus/empty-segment handling, "
            "position provenance and determinate/uncertain mass states."
        ),
    )
    app.include_router(router)
    register_error_handlers(app)

    logger.info(
        "application created",
        extra={"extra_fields": {"event": "app_create", "service_version": __version__}},
    )
    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
