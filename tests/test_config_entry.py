"""Configuration validation and runnable entrypoint smoke tests."""

from __future__ import annotations

import importlib
import os

import pytest


def test_invalid_budget_env_is_rejected(monkeypatch):
    from app import config as config_module

    monkeypatch.setenv("FIM_DEFAULT_BUDGET_NODES", "not-a-number")
    with pytest.raises(ValueError, match="must be an integer"):
        config_module.get_settings()

    monkeypatch.setenv("FIM_DEFAULT_BUDGET_NODES", "0")
    with pytest.raises(ValueError, match=">= 1"):
        config_module.get_settings()


def test_settings_read_environment(monkeypatch, tmp_path):
    from app import config as config_module

    db = tmp_path / "x.db"
    monkeypatch.setenv("FIM_DB_PATH", str(db))
    monkeypatch.setenv("FIM_DEFAULT_BUDGET_NODES", "7")
    monkeypatch.setenv("FIM_MAX_BUDGET_NODES", "99")
    monkeypatch.setenv("FIM_LOG_LEVEL", "debug")
    settings = config_module.get_settings()
    assert settings.database_path == str(db)
    assert settings.default_budget_nodes == 7
    assert settings.max_budget_nodes == 99
    assert settings.log_level == "DEBUG"


def test_main_build_app_is_runnable(monkeypatch, tmp_path):
    monkeypatch.setenv("FIM_DB_PATH", str(tmp_path / "entry.db"))
    from app import main as main_module

    app = main_module.build_app()
    assert app.title == "Closed Frequent Itemset Backend"

    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert os.path.exists(tmp_path / "entry.db")
