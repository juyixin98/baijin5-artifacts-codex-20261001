"""Configuration loading and validation for the local SCRAM service.

Configuration is read from a standalone TOML file (``config/config.toml`` by
default), never from code constants.  Values are validated at startup so a
misconfigured deployment fails fast.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

# Minimum iteration count mandated by RFC 7677 section 4.
RFC7677_MIN_ITERATIONS = 4096
SUPPORTED_CB_MODES = ("none", "tls-server-end-point")


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    database_path: str
    audit_log_path: str


@dataclass(frozen=True)
class CryptoConfig:
    mechanism: str
    plus_mechanism: str
    iteration_count: int
    salt_bytes: int
    server_nonce_bytes: int
    client_nonce_min_bytes: int
    nonce_max_bytes: int


@dataclass(frozen=True)
class SessionConfig:
    ttl_seconds: int
    max_active: int


@dataclass(frozen=True)
class LimitsConfig:
    max_message_bytes: int
    max_attributes: int
    max_username_chars: int


@dataclass(frozen=True)
class ChannelBindingConfig:
    # "none" or "tls-server-end-point" — the complete advertised scope.
    mode: str
    cert_hash_header: str


@dataclass(frozen=True)
class AppConfig:
    server: ServerConfig
    crypto: CryptoConfig
    session: SessionConfig
    limits: LimitsConfig
    channel_binding: ChannelBindingConfig
    source_path: str

    @property
    def offers_plus(self) -> bool:
        return self.channel_binding.mode == "tls-server-end-point"


def _require_positive(name: str, value: int) -> None:
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"config value {name!r} must be a positive integer, got {value!r}")


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    """Load and validate configuration from TOML.

    Raises ``ValueError`` (startup fail-fast) on missing keys or unsafe values.
    """
    if path is None:
        path = os.environ.get("SCRAM_CONFIG", "config/config.toml")
    path = Path(path)
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    try:
        srv, cry, ses, lim, cb = raw["server"], raw["crypto"], raw["session"], raw["limits"], raw["channel_binding"]
    except KeyError as exc:
        raise ValueError(f"missing config section: [{exc.args[0]}]") from exc

    if cry.get("mechanism") != "SCRAM-SHA-256" or cry.get("plus_mechanism") != "SCRAM-SHA-256-PLUS":
        raise ValueError("only SCRAM-SHA-256 / SCRAM-SHA-256-PLUS mechanism names are supported")
    iterations = int(cry["iteration_count"])
    if iterations < RFC7677_MIN_ITERATIONS:
        raise ValueError(f"iteration_count {iterations} is below RFC 7677 minimum {RFC7677_MIN_ITERATIONS}")
    for name in ("salt_bytes", "server_nonce_bytes", "nonce_max_bytes"):
        _require_positive(f"crypto.{name}", int(cry[name]))
    if int(cry["client_nonce_min_bytes"]) < 1:
        raise ValueError("crypto.client_nonce_min_bytes must be >= 1")
    if int(cry["server_nonce_bytes"]) < 16:
        raise ValueError("crypto.server_nonce_bytes must be >= 16 for nonce uniqueness margins")

    cb_mode = str(cb["mode"])
    if cb_mode not in SUPPORTED_CB_MODES:
        raise ValueError(f"channel_binding.mode must be one of {SUPPORTED_CB_MODES}, got {cb_mode!r}")

    for section, keys in (
        (ses, ("ttl_seconds", "max_active")),
        (lim, ("max_message_bytes", "max_attributes", "max_username_chars")),
    ):
        for key in keys:
            _require_positive(f"{section is ses and 'session' or 'limits'}.{key}", int(section[key]))

    return AppConfig(
        server=ServerConfig(
            host=str(srv["host"]),
            port=int(srv["port"]),
            database_path=str(srv["database_path"]),
            audit_log_path=str(srv["audit_log_path"]),
        ),
        crypto=CryptoConfig(
            mechanism=str(cry["mechanism"]),
            plus_mechanism=str(cry["plus_mechanism"]),
            iteration_count=iterations,
            salt_bytes=int(cry["salt_bytes"]),
            server_nonce_bytes=int(cry["server_nonce_bytes"]),
            client_nonce_min_bytes=int(cry["client_nonce_min_bytes"]),
            nonce_max_bytes=int(cry["nonce_max_bytes"]),
        ),
        session=SessionConfig(ttl_seconds=int(ses["ttl_seconds"]), max_active=int(ses["max_active"])),
        limits=LimitsConfig(
            max_message_bytes=int(lim["max_message_bytes"]),
            max_attributes=int(lim["max_attributes"]),
            max_username_chars=int(lim["max_username_chars"]),
        ),
        channel_binding=ChannelBindingConfig(mode=cb_mode, cert_hash_header=str(cb["cert_hash_header"])),
        source_path=str(path),
    )
