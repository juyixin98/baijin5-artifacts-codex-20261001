"""Configuration loading and fail-fast validation tests."""
from __future__ import annotations

import tomllib

import pytest

from scram_auth.config import load_config


def _write(tmp_path, mapping: dict) -> str:
    # Minimal hand-rolled TOML writer for the flat sections used in tests.
    lines: list[str] = []
    for section, pairs in mapping.items():
        lines.append(f"[{section}]")
        for key, value in pairs.items():
            if isinstance(value, str):
                lines.append(f'{key} = "{value}"')
            else:
                lines.append(f"{key} = {value}")
        lines.append("")
    path = tmp_path / "config.toml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _valid_mapping() -> dict:
    return {
        "server": {"host": "127.0.0.1", "port": 8765, "database_path": "x.db", "audit_log_path": "a.jsonl"},
        "crypto": {
            "mechanism": "SCRAM-SHA-256",
            "plus_mechanism": "SCRAM-SHA-256-PLUS",
            "iteration_count": 4096,
            "salt_bytes": 32,
            "server_nonce_bytes": 24,
            "client_nonce_min_bytes": 16,
            "nonce_max_bytes": 128,
        },
        "session": {"ttl_seconds": 120, "max_active": 4096},
        "limits": {"max_message_bytes": 4096, "max_attributes": 8, "max_username_chars": 128},
        "channel_binding": {"mode": "none", "cert_hash_header": "X-Tls-Server-End-Point-Sha256"},
    }


def test_shipped_config_loads_and_pins_rfc_minimum() -> None:
    cfg = load_config("config/config.toml")
    assert cfg.crypto.iteration_count >= 4096
    assert cfg.channel_binding.mode == "none"
    assert cfg.offers_plus is False
    assert cfg.server.host == "127.0.0.1"


def test_plus_mode_is_recognised(tmp_path) -> None:
    mapping = _valid_mapping()
    mapping["channel_binding"]["mode"] = "tls-server-end-point"
    cfg = load_config(_write(tmp_path, mapping))
    assert cfg.offers_plus is True


def test_iteration_count_below_rfc_floor_is_rejected(tmp_path) -> None:
    mapping = _valid_mapping()
    mapping["crypto"]["iteration_count"] = 1024
    with pytest.raises(ValueError, match="RFC 7677"):
        load_config(_write(tmp_path, mapping))


def test_unknown_mechanism_name_rejected(tmp_path) -> None:
    mapping = _valid_mapping()
    mapping["crypto"]["mechanism"] = "SCRAM-SHA-1"
    with pytest.raises(ValueError, match="SCRAM-SHA-256"):
        load_config(_write(tmp_path, mapping))


def test_unknown_cb_mode_rejected(tmp_path) -> None:
    mapping = _valid_mapping()
    mapping["channel_binding"]["mode"] = "tls-unique"
    with pytest.raises(ValueError, match="channel_binding.mode"):
        load_config(_write(tmp_path, mapping))


def test_missing_section_fails_fast(tmp_path) -> None:
    mapping = _valid_mapping()
    del mapping["session"]
    with pytest.raises(ValueError, match="missing config section"):
        load_config(_write(tmp_path, mapping))


def test_shipped_config_is_valid_toml_with_expected_keys() -> None:
    with open("config/config.toml", "rb") as fh:
        raw = tomllib.load(fh)
    assert raw["crypto"]["salt_bytes"] == 32
    assert raw["limits"]["max_message_bytes"] >= 4096
