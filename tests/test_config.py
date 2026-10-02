"""Config layer: YAML defaults, env override precedence, unknown keys."""

from __future__ import annotations

import pytest

from watershed_backend.config import Settings, load_settings


def test_defaults_when_nothing_configured(tmp_path):
    settings = load_settings(config_path=tmp_path / "missing.yaml", environ={})
    assert settings == Settings()


def test_yaml_values_are_loaded(tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text("default_connectivity: 4\nmax_pixels: 100\n", encoding="utf-8")
    settings = load_settings(config_path=path, environ={})
    assert settings.default_connectivity == 4
    assert settings.max_pixels == 100


def test_env_overrides_yaml(tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text("default_connectivity: 4\n", encoding="utf-8")
    settings = load_settings(
        config_path=path, environ={"WATERSHED_DEFAULT_CONNECTIVITY": "8"}
    )
    assert settings.default_connectivity == 8


def test_unknown_keys_are_rejected(tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text("surprise: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown config keys"):
        load_settings(config_path=path, environ={})
