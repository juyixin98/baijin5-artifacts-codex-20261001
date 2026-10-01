"""Dependency injection for settings and the run store.

Both are process-wide singletons, lazily constructed from the environment on
first use. Tests and scripts pin explicit instances with :func:`configure`
(or via FastAPI ``dependency_overrides``) and restore with :func:`reset`.
"""
from __future__ import annotations

from app.config import Settings
from app.store import RunStore

_settings: Settings | None = None
_store: RunStore | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def get_store() -> RunStore:
    global _store
    if _store is None:
        _store = RunStore(get_settings().db_path)
    return _store


def configure(settings: Settings, store: RunStore | None = None) -> RunStore:
    """Pin explicit singletons; returns the (provided or created) store."""
    global _settings, _store
    _settings = settings
    _store = store if store is not None else RunStore(settings.db_path)
    return _store


def reset() -> None:
    global _settings, _store
    _settings = None
    _store = None
