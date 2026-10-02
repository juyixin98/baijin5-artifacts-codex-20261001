"""服务配置。全部可通过环境变量覆盖，默认值面向本地运行。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_root: Path = Path("data")
    tile_size: int = 256
    max_levels: int = 16
    max_image_pixels: int = 400_000_000
    max_region_pixels: int = 4_000_000
    # JSON 格式返回区域的像素上限（超出请用 format=npy）
    max_json_pixels: int = 262_144
    log_file: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            data_root=Path(env.get("PYRAMID_DATA_ROOT", "data")),
            tile_size=int(env.get("PYRAMID_TILE_SIZE", "256")),
            max_levels=int(env.get("PYRAMID_MAX_LEVELS", "16")),
            max_image_pixels=int(env.get("PYRAMID_MAX_IMAGE_PIXELS", "400000000")),
            max_region_pixels=int(env.get("PYRAMID_MAX_REGION_PIXELS", "4000000")),
            max_json_pixels=int(env.get("PYRAMID_MAX_JSON_PIXELS", "262144")),
            log_file=env.get("PYRAMID_LOG_FILE") or None,
        )
