"""Tile store: atomic level publication and integrity-checked reads.

Layout on disk::

    <root>/<pyramid_id>/
        source.json                 # source description + build run id
        levels/<L>/manifest.json    # level metadata + per-tile sha256
        levels/<L>/tiles/<ty>_<tx>.npy

Publication protocol
--------------------
Tiles and the manifest are written into a staging directory
``levels/.tmp-<level>-<run_id>/`` and the directory is then renamed to
``levels/<level>`` in a single ``os.rename``.  Readers therefore observe a
level either fully published (manifest + all tiles) or not at all.  A level
that already exists is never overwritten — re-publication is a state
conflict, and a failed publish leaves no visible level behind.

Integrity
---------
Every tile read verifies the sha256 recorded in the manifest, plus the
decoded shape and dtype.  Any mismatch raises :class:`TileIntegrityError`;
the store never substitutes placeholder pixels.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import numpy as np

from .contracts import PIXEL_DTYPE, LevelMeta, TileRecord, validate_pyramid_id
from .coords import tile_bounds
from .errors import (
    ComputeError,
    NotFoundError,
    StateConflictError,
    TileIntegrityError,
)

MANIFEST_NAME = "manifest.json"
SOURCE_NAME = "source.json"


class TileStore:
    def __init__(self, root: os.PathLike | str) -> None:
        self.root = Path(root)
        self._manifest_cache: Dict[Tuple[str, int], LevelMeta] = {}

    # ------------------------------------------------------------------ paths
    def pyramid_dir(self, pyramid_id: str) -> Path:
        return self.root / validate_pyramid_id(pyramid_id)

    def level_dir(self, pyramid_id: str, level: int) -> Path:
        return self.pyramid_dir(pyramid_id) / "levels" / str(level)

    # --------------------------------------------------------------- mutation
    def init_pyramid(self, pyramid_id: str, source_info: dict) -> None:
        """Create the pyramid directory; fails if it already exists."""
        pdir = self.pyramid_dir(pyramid_id)
        if pdir.exists():
            raise StateConflictError(
                f"pyramid {pyramid_id!r} already exists at {pdir}",
                detail={"pyramid_id": pyramid_id},
            )
        (pdir / "levels").mkdir(parents=True)
        payload = dict(source_info)
        payload["created_utc"] = datetime.now(timezone.utc).isoformat()
        tmp = pdir / f".{SOURCE_NAME}.tmp"
        try:
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
            os.replace(tmp, pdir / SOURCE_NAME)
        except Exception:
            # Do not strand an id-blocking directory without a source record.
            shutil.rmtree(pdir, ignore_errors=True)
            raise

    def publish_level(
        self,
        pyramid_id: str,
        meta: LevelMeta,
        tiles: Iterable[Tuple[int, int, np.ndarray]],
    ) -> LevelMeta:
        """Atomically publish one level; returns the manifest metadata.

        ``tiles`` yields ``(tx, ty, array)`` triples.  Arrays must match the
        contracted tile shape ``(th, tw, channels)`` and ``float64`` dtype.
        """
        final_dir = self.level_dir(pyramid_id, meta.level)
        if final_dir.exists():
            raise StateConflictError(
                f"level {meta.level} of pyramid {pyramid_id!r} is already "
                "published",
                detail={"pyramid_id": pyramid_id, "level": meta.level},
            )
        staging = final_dir.parent / f".tmp-{meta.level}-{meta.run_id}"
        # Clear stale staging dirs from crashed earlier publishes.
        for stale in final_dir.parent.glob(f".tmp-{meta.level}-*"):
            shutil.rmtree(stale, ignore_errors=True)
        (staging / "tiles").mkdir(parents=True)

        records: List[TileRecord] = []
        try:
            for tx, ty, arr in tiles:
                records.append(
                    self._write_tile(staging, meta, tx, ty, arr)
                )
            expected = meta.tiles_x * meta.tiles_y
            covered = {(r.tx, r.ty) for r in records}
            if len(records) != expected or len(covered) != expected:
                raise ComputeError(
                    f"level {meta.level} of pyramid {pyramid_id!r} yielded "
                    f"{len(records)} tiles ({len(covered)} unique), expected "
                    f"{expected}; refusing to publish an incomplete level"
                )
            published = LevelMeta(**{**meta.__dict__, "tiles": tuple(records)})
            manifest_path = staging / MANIFEST_NAME
            manifest_path.write_text(
                json.dumps(published.to_dict(), indent=2, sort_keys=True)
            )
            os.rename(staging, final_dir)  # atomic publish
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        self._manifest_cache[(pyramid_id, meta.level)] = published
        return published

    def _write_tile(
        self, staging: Path, meta: LevelMeta, tx: int, ty: int, arr: np.ndarray
    ) -> TileRecord:
        x0, y0, tw, th = tile_bounds(
            tx, ty, meta.tile_size, meta.width, meta.height
        )
        expected = (th, tw, meta.channels)
        if arr.shape != expected:
            raise ComputeError(
                f"tile ({tx}, {ty}) of level {meta.level} has shape "
                f"{arr.shape}, expected {expected}"
            )
        if arr.dtype != np.dtype(PIXEL_DTYPE):
            raise ComputeError(
                f"tile ({tx}, {ty}) of level {meta.level} has dtype "
                f"{arr.dtype}, expected {PIXEL_DTYPE}"
            )
        buf = io.BytesIO()
        np.save(buf, arr, allow_pickle=False)
        data = buf.getvalue()
        rel = f"tiles/{ty}_{tx}.npy"
        (staging / rel).write_bytes(data)
        return TileRecord(
            tx=tx,
            ty=ty,
            x0=x0,
            y0=y0,
            width=tw,
            height=th,
            path=rel,
            sha256=hashlib.sha256(data).hexdigest(),
        )

    # ----------------------------------------------------------------- reads
    def load_manifest(self, pyramid_id: str, level: int) -> LevelMeta:
        key = (pyramid_id, level)
        if key in self._manifest_cache:
            return self._manifest_cache[key]
        mpath = self.level_dir(pyramid_id, level) / MANIFEST_NAME
        if not mpath.exists():
            raise NotFoundError(
                f"level {level} of pyramid {pyramid_id!r} is not published",
                detail={"pyramid_id": pyramid_id, "level": level},
            )
        try:
            meta = LevelMeta.from_dict(json.loads(mpath.read_text()))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            raise TileIntegrityError(
                f"manifest of pyramid {pyramid_id!r} level {level} is "
                f"corrupt: {exc}"
            ) from exc
        self._manifest_cache[key] = meta
        return meta

    def read_tile(self, pyramid_id: str, level: int, tx: int, ty: int) -> np.ndarray:
        meta = self.load_manifest(pyramid_id, level)
        try:
            rec = meta.tile_record(tx, ty)
        except KeyError as exc:
            raise NotFoundError(
                f"tile ({tx}, {ty}) not in manifest of pyramid "
                f"{pyramid_id!r} level {level}"
            ) from exc
        path = self.level_dir(pyramid_id, level) / rec.path
        # A tampered manifest must not be able to steer reads outside the
        # level directory.
        rel = Path(rec.path)
        if rel.is_absolute() or ".." in rel.parts:
            raise TileIntegrityError(
                f"manifest of pyramid {pyramid_id!r} level {level} contains "
                f"an illegal tile path {rec.path!r}"
            )
        if not path.exists():
            raise TileIntegrityError(
                f"tile file {path} listed in manifest is missing"
            )
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != rec.sha256:
            raise TileIntegrityError(
                f"tile ({tx}, {ty}) of pyramid {pyramid_id!r} level {level} "
                f"failed sha256 verification",
                detail={"expected": rec.sha256, "actual": digest},
            )
        try:
            arr = np.load(io.BytesIO(data), allow_pickle=False)
        except ValueError as exc:
            raise TileIntegrityError(
                f"tile ({tx}, {ty}) of pyramid {pyramid_id!r} level {level} "
                f"cannot be decoded: {exc}"
            ) from exc
        expected = (rec.height, rec.width, meta.channels)
        if arr.shape != expected or arr.dtype != np.dtype(PIXEL_DTYPE):
            raise TileIntegrityError(
                f"tile ({tx}, {ty}) of pyramid {pyramid_id!r} level {level} "
                f"has shape/dtype {arr.shape}/{arr.dtype}, expected "
                f"{expected}/{PIXEL_DTYPE}"
            )
        return arr

    def list_levels(self, pyramid_id: str) -> List[int]:
        pdir = self.pyramid_dir(pyramid_id)
        if not pdir.exists():
            raise NotFoundError(f"pyramid {pyramid_id!r} does not exist")
        levels_dir = pdir / "levels"
        out = []
        for child in levels_dir.iterdir():
            if child.name.startswith(".tmp-") or not child.name.isdigit():
                continue  # staging dirs and strays are never visible as levels
            if child.is_dir() and (child / MANIFEST_NAME).exists():
                out.append(int(child.name))
        return sorted(out)

    def source_info(self, pyramid_id: str) -> dict:
        path = self.pyramid_dir(pyramid_id) / SOURCE_NAME
        if not path.exists():
            raise NotFoundError(f"pyramid {pyramid_id!r} does not exist")
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise TileIntegrityError(
                f"source record of pyramid {pyramid_id!r} is corrupt: {exc}"
            ) from exc

    # ------------------------------------------------------------- validation
    def verify_level(self, pyramid_id: str, level: int) -> dict:
        """Re-verify every tile of a level against its manifest.

        The manifest is re-read from disk (cache bypassed) so that on-disk
        tampering after the first read is also detected.
        """
        self._manifest_cache.pop((pyramid_id, level), None)
        meta = self.load_manifest(pyramid_id, level)
        bad: List[dict] = []
        for rec in meta.tiles:
            try:
                self.read_tile(pyramid_id, level, rec.tx, rec.ty)
            except TileIntegrityError as exc:
                bad.append({"tx": rec.tx, "ty": rec.ty, "reason": exc.message})
        return {
            "pyramid_id": pyramid_id,
            "level": level,
            "ok": not bad,
            "checked": len(meta.tiles),
            "bad": bad,
        }
