"""Shared pytest fixtures."""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from scram_auth.audit import InMemoryAuditLogger
from scram_auth.config import load_config
from scram_auth.server import ScramServerStateMachine
from scram_auth.verifiers import VerifierRepository

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def rfc_vector() -> dict:
    with (FIXTURES / "rfc7677_vector.json").open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def config(tmp_path):
    cfg_path = Path("config/config.toml")
    cfg = load_config(cfg_path)
    # Redirect stateful artefacts into the per-test temp directory.
    object.__setattr__(
        cfg,
        "server",
        type(cfg.server)(
            host=cfg.server.host,
            port=cfg.server.port,
            database_path=str(tmp_path / "test.db"),
            audit_log_path=str(tmp_path / "audit.jsonl"),
        ),
    )
    return cfg


@pytest.fixture
def plus_config(config):
    object.__setattr__(
        config,
        "channel_binding",
        type(config.channel_binding)(mode="tls-server-end-point", cert_hash_header=config.channel_binding.cert_hash_header),
    )
    return config


@pytest.fixture
def audit(config) -> InMemoryAuditLogger:
    return InMemoryAuditLogger(config.server.audit_log_path)


@pytest.fixture
def repository(config) -> VerifierRepository:
    repo = VerifierRepository(config.server.database_path)
    yield repo
    repo.close()


@pytest.fixture
def server(config, repository, audit) -> ScramServerStateMachine:
    return ScramServerStateMachine(config, repository, audit)
