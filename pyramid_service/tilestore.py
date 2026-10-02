"""瓦片存储：层级内容与元数据的原子发布、校验和读取。

目录布局：
    <root>/<image_id>/levels/<level>/meta.json      层级元数据 + 每瓦片 sha256
    <root>/<image_id>/levels/<level>/tiles/TTTT_TTTT.npy
    <root>/<image_id>/image.json                   图像级元数据（最后落盘）

发布协议：
1. 全部瓦片与 meta.json 先写入临时目录 levels/.<level>.tmp-<uuid>/；
2. 临时目录 os.rename 为 levels/<level>/ ——  rename 是唯一的可见性提交点，
   读者只会看到"完全不存在"或"完整发布"两种状态；
3. 任何中途失败清理临时目录，已发布层级不受影响。

读取协议：每次读瓦片都校验 sha256，校验失败抛 IntegrityError，
绝不返回全黑或部分数据冒充成功。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np

from .contracts import DTYPE, LevelMeta
from .errors import (
    ComputeError,
    ImageNotFoundError,
    IntegrityError,
    InputError,
    LevelNotFoundError,
    StateConflictError,
)


def _tile_name(ty: int, tx: int) -> str:
    return f"{ty:04d}_{tx:04d}"


class TileStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    # ---- 路径 ----
    def image_dir(self, image_id: str) -> Path:
        return self.root / image_id

    def levels_dir(self, image_id: str) -> Path:
        return self.image_dir(image_id) / "levels"

    def level_dir(self, image_id: str, level: int) -> Path:
        return self.levels_dir(image_id) / str(level)

    # ---- 发布 ----
    def publish_level(
        self,
        image_id: str,
        level_meta: LevelMeta,
        tiles: Iterable[tuple[int, int, np.ndarray]],
    ) -> dict:
        """原子发布一个层级。tiles 产出 (ty, tx, array)；全部写完后一次性生效。

        返回落盘的层级元数据 dict（含每瓦片 sha256）。
        """
        final_dir = self.level_dir(image_id, level_meta.level)
        if final_dir.exists():
            raise StateConflictError(
                f"level {level_meta.level} of image {image_id} already published"
            )
        tmp_dir = self.levels_dir(image_id) / f".{level_meta.level}.tmp-{uuid.uuid4().hex}"
        tiles_dir = tmp_dir / "tiles"
        tiles_dir.mkdir(parents=True)
        tile_entries: dict[str, dict] = {}
        try:
            for ty, tx, arr in tiles:
                arr = np.ascontiguousarray(arr, dtype=np.float32)
                name = _tile_name(ty, tx)
                buf = io.BytesIO()
                np.save(buf, arr, allow_pickle=False)
                payload = buf.getvalue()
                digest = hashlib.sha256(payload).hexdigest()
                path = tiles_dir / f"{name}.npy"
                with open(path, "wb") as fh:
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())
                tile_entries[name] = {
                    "sha256": digest,
                    "shape": [int(arr.shape[0]), int(arr.shape[1])],
                    "nbytes": len(payload),
                }
            meta = {
                **level_meta.to_dict(),
                "dtype": DTYPE,
                "tiles": tile_entries,
            }
            meta_path = tmp_dir / "meta.json"
            with open(meta_path, "w", encoding="utf-8") as fh:
                json.dump(meta, fh, ensure_ascii=False, indent=1, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.rename(tmp_dir, final_dir)  # 原子提交点
        except BaseException:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
        return meta

    def write_image_meta(self, image_id: str, meta_dict: dict) -> None:
        """图像级元数据：tmp 文件 + os.replace 原子落盘。"""
        self.image_dir(image_id).mkdir(parents=True, exist_ok=True)
        final = self.image_dir(image_id) / "image.json"
        tmp = self.image_dir(image_id) / f".image.json.tmp-{uuid.uuid4().hex}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(meta_dict, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, final)

    # ---- 读取 ----
    def read_image_meta(self, image_id: str) -> dict:
        path = self.image_dir(image_id) / "image.json"
        if not path.is_file():
            raise ImageNotFoundError(f"image {image_id!r} not found")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError) as exc:
            raise ComputeError(f"corrupt image metadata for {image_id!r}: {exc}") from exc

    def list_images(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(
            p.name for p in self.root.iterdir() if (p / "image.json").is_file()
        )

    def read_level_meta(self, image_id: str, level: int) -> dict:
        path = self.level_dir(image_id, level) / "meta.json"
        if not path.is_file():
            raise LevelNotFoundError(
                f"level {level} of image {image_id!r} is not published"
            )
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
            for key in ("level", "width", "height", "tile_size", "tiles"):
                meta[key]
        except (json.JSONDecodeError, KeyError) as exc:
            raise ComputeError(
                f"corrupt level metadata image={image_id} level={level}: {exc}"
            ) from exc
        return meta

    def read_tile(
        self, image_id: str, level: int, ty: int, tx: int, *, meta: dict | None = None
    ) -> np.ndarray:
        if meta is None:
            meta = self.read_level_meta(image_id, level)
        name = _tile_name(ty, tx)
        entry = meta["tiles"].get(name)
        if entry is None:
            raise ComputeError(
                f"tile {name} missing from level metadata "
                f"(image={image_id}, level={level})"
            )
        path = self.level_dir(image_id, level) / "tiles" / f"{name}.npy"
        if not path.is_file():
            raise IntegrityError(
                f"tile file missing on disk: image={image_id} level={level} tile={name}"
            )
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != entry["sha256"]:
            raise IntegrityError(
                f"tile checksum mismatch: image={image_id} level={level} tile={name} "
                f"expected={entry['sha256'][:16]} actual={digest[:16]}"
            )
        try:
            arr = np.load(io.BytesIO(payload), allow_pickle=False)
        except Exception as exc:
            raise IntegrityError(
                f"tile unreadable: image={image_id} level={level} tile={name}: {exc}"
            ) from exc
        if list(arr.shape) != entry["shape"] or arr.dtype != np.float32:
            raise IntegrityError(
                f"tile shape/dtype mismatch: image={image_id} level={level} tile={name} "
                f"expected shape={entry['shape']} dtype=float32, "
                f"got shape={list(arr.shape)} dtype={arr.dtype}"
            )
        return arr

    def iter_tiles(self, image_id: str, level: int) -> Iterator[tuple[int, int, np.ndarray]]:
        meta = self.read_level_meta(image_id, level)
        for name in sorted(meta["tiles"]):
            ty_s, tx_s = name.split("_")
            yield int(ty_s), int(tx_s), self.read_tile(
                image_id, level, int(ty_s), int(tx_s), meta=meta
            )

    def read_region(
        self, image_id: str, level: int, x: int, y: int, w: int, h: int
    ) -> np.ndarray:
        """精确拼接跨瓦片区域，返回 float32 (h, w)。"""
        meta = self.read_level_meta(image_id, level)
        lw, lh = int(meta["width"]), int(meta["height"])
        if w < 1 or h < 1 or x < 0 or y < 0 or x + w > lw or y + h > lh:
            raise InputError(
                f"region (x={x}, y={y}, w={w}, h={h}) outside level {level} "
                f"bounds {lw}x{lh} of image {image_id!r}"
            )
        ts = int(meta["tile_size"])
        out = np.zeros((h, w), dtype=np.float32)
        ty0, ty1 = y // ts, (y + h - 1) // ts
        tx0, tx1 = x // ts, (x + w - 1) // ts
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                tile = self.read_tile(image_id, level, ty, tx, meta=meta)
                gy0, gx0 = ty * ts, tx * ts
                oy0, ox0 = max(y, gy0), max(x, gx0)
                oy1 = min(y + h, gy0 + tile.shape[0])
                ox1 = min(x + w, gx0 + tile.shape[1])
                out[oy0 - y : oy1 - y, ox0 - x : ox1 - x] = tile[
                    oy0 - gy0 : oy1 - gy0, ox0 - gx0 : ox1 - gx0
                ]
        return out
