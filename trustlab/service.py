"""Composition root: wires store, bundle manager and data plane together.

Also the runnable service configuration. Kept deliberately thin: all
policy lives in the boundary modules (bundle / tls_plane / store).
"""
from __future__ import annotations

import dataclasses
import os
import stat
import uuid
from pathlib import Path

from . import frames
from .bundle import BundleManager
from .store import Store, utcnow
from .tls_plane import TLSDataPlane


@dataclasses.dataclass
class ServiceConfig:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 0  # 0 = ephemeral
    max_connections: int = 64
    max_frame_bytes: int = frames.DEFAULT_MAX_FRAME_BYTES
    run_id: str | None = None


class TrustLabService:
    def __init__(self, config: ServiceConfig, *, server_cert_pem: str,
                 server_key_pem: str, initial_roots_pem: list[str]) -> None:
        self.config = config
        self.run_id = config.run_id or f"run-{uuid.uuid4().hex[:12]}"
        self.started_at = utcnow()
        data_dir = Path(config.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)

        cert_path = data_dir / "server.crt.pem"
        key_path = data_dir / "server.key.pem"
        cert_path.write_text(server_cert_pem)
        key_path.write_text(server_key_pem)
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)

        self.store = Store(data_dir / "trustlab.db")
        self.data_plane = TLSDataPlane(
            store=self.store,
            run_id=self.run_id,
            host=config.host,
            port=config.port,
            server_cert_path=str(cert_path),
            server_key_path=str(key_path),
            max_connections=config.max_connections,
            max_frame_bytes=config.max_frame_bytes,
        )
        self.bundles = BundleManager(
            self.store,
            run_id=self.run_id,
            on_activated=self.data_plane.update_trust,
        )
        self.bundles.create(initial_roots_pem, kind="initial",
                            note="initial trust bundle")

    @property
    def host(self) -> str:
        return self.config.host

    @property
    def port(self) -> int:
        return self.data_plane.port

    def start(self) -> None:
        self.data_plane.start()
        self.store.audit(
            run_id=self.run_id,
            category="STATE_CHANGE",
            event="service_started",
            detail={"host": self.host, "port": self.port,
                    "run_id": self.run_id},
            reasoning="data plane listening; control plane may attach "
                      "separately",
        )

    def stop(self) -> None:
        self.data_plane.stop()
        self.store.close()
