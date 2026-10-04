"""Composition root: build the service from settings once per process."""

from __future__ import annotations

import logging
from functools import lru_cache

from ..config import Settings, ensure_base_dirs
from ..core.audit import AuditLog
from ..core.crypto import get_backend
from ..core.keyring import KeyBundle, load_keys
from ..core.service import SegmentedAEADService
from ..core.staging import StagingArea
from ..db.store import Store


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


@lru_cache(maxsize=1)
def get_bundle() -> KeyBundle:
    settings = get_settings()
    return load_keys(settings.key_file)


@lru_cache(maxsize=1)
def get_store() -> Store:
    return Store(get_settings().db_path)


@lru_cache(maxsize=1)
def get_service() -> SegmentedAEADService:
    settings = get_settings()
    ensure_base_dirs(settings)
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    bundle = get_bundle()
    store = get_store()
    staging = StagingArea(settings.staging_dir, settings.release_dir)
    service = SegmentedAEADService(
        store, staging, bundle, get_backend(settings.backend),
        audit=AuditLog(),
        max_segment_bytes=settings.max_segment_bytes,
        max_total_bytes=settings.max_total_bytes)
    service.recover_interrupted()
    return service
