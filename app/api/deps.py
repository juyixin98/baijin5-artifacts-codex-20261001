"""Shared dependencies (settings, SQLite store)."""

from __future__ import annotations

from functools import lru_cache

from app.config import Settings, get_settings
from app.storage.store import DigestStore


@lru_cache(maxsize=1)
def get_cached_settings() -> Settings:
    return get_settings()


@lru_cache(maxsize=1)
def get_cached_store() -> DigestStore:
    settings = get_cached_settings()
    return DigestStore(settings.db_path)


def settings_dependency() -> Settings:
    return get_cached_settings()


def store_dependency() -> DigestStore:
    return get_cached_store()
