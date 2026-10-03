"""Configuration layer.

Settings come from environment variables with sane local defaults; nothing
requires production accounts or external services. The workspace holds all
state (images, jobs, kernels, logs, reports) so runs are reproducible and
inspectable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    workspace: Path
    default_tile_shape: Tuple[int, int] = (256, 256)
    compare_atol: float = 1e-9
    compare_rtol: float = 1e-9
    memory_cap_mb: int = 512

    @classmethod
    def from_env(cls) -> "Settings":
        workspace = Path(os.environ.get("TILECONV_WORKSPACE", "./workspace")).resolve()
        tile_h = _int_env("TILECONV_TILE_H", 256)
        tile_w = _int_env("TILECONV_TILE_W", 256)
        return cls(
            workspace=workspace,
            default_tile_shape=(tile_h, tile_w),
            compare_atol=float(os.environ.get("TILECONV_ATOL", "1e-9")),
            compare_rtol=float(os.environ.get("TILECONV_RTOL", "1e-9")),
            memory_cap_mb=_int_env("TILECONV_MEMORY_CAP_MB", 512),
        )

    @property
    def images_dir(self) -> Path:
        return self.workspace / "images"

    @property
    def jobs_dir(self) -> Path:
        return self.workspace / "jobs"

    @property
    def kernels_dir(self) -> Path:
        return self.workspace / "kernels"

    @property
    def logs_dir(self) -> Path:
        return self.workspace / "logs"

    @property
    def reports_dir(self) -> Path:
        return self.workspace / "reports"

    def ensure_dirs(self) -> None:
        for d in (self.images_dir, self.jobs_dir, self.kernels_dir,
                  self.logs_dir, self.reports_dir):
            d.mkdir(parents=True, exist_ok=True)
