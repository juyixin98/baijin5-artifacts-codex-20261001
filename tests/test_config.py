"""Tests for configuration loading and validation."""

from __future__ import annotations

import pytest

from tensorcraft.config import load_config
from tensorcraft.errors import ConfigError


def test_loads_bundled_defaults():
    config = load_config()
    assert config.server.host == "127.0.0.1"
    assert config.semantics.overlap_write_policy in {"reject", "temp_copy"}
    assert config.storage.max_elements > 0


def test_rejects_non_positive_port(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("server:\n  port: 0\n")
    with pytest.raises(ConfigError):
        load_config(path)


def test_rejects_bad_overlap_policy(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("semantics:\n  overlap_write_policy: oops\n")
    with pytest.raises(ConfigError):
        load_config(path)


def test_rejects_malformed_yaml(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("server: [unclosed\n")
    with pytest.raises(ConfigError):
        load_config(path)


def test_rejects_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")


def test_override_returns_new_config():
    config = load_config()
    changed = config.with_overrides(**{"server.port": 9000})
    assert changed.server.port == 9000
    assert config.server.port != 9000  # original immutable
