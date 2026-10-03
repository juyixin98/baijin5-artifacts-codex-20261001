"""Local memmap-backed image store.

Images live as ``.npy`` files opened with ``numpy.memmap`` so arrays larger
than RAM stay on disk and only touched pages become resident. Every image has
a JSON sidecar holding its :class:`ImageSpec` (shape, dtype, digest, meta).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .contract import ImageSpec, digest_array
from .errors import InvalidSpecError, NotFoundError


class ImageStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.images_dir = self.root / "images"
        self.images_dir.mkdir(parents=True, exist_ok=True)

    def _spec_path(self, image_id: str) -> Path:
        return self.images_dir / f"{image_id}.json"

    def create_image(self, array: np.ndarray, meta: Optional[dict[str, Any]] = None) -> ImageSpec:
        array = np.asarray(array)
        if array.ndim != 2:
            raise InvalidSpecError("only 2-D images are supported",
                                   {"ndim": array.ndim})
        image_id = uuid.uuid4().hex[:12]
        path = self.images_dir / f"{image_id}.npy"
        mm = np.lib.format.open_memmap(path, mode="w+", dtype=array.dtype, shape=array.shape)
        mm[:] = array
        mm.flush()
        spec = ImageSpec(
            image_id=image_id,
            shape=(int(array.shape[0]), int(array.shape[1])),
            dtype=str(array.dtype),
            path=str(path),
            digest=digest_array(mm),
            meta=meta or {},
        )
        self._spec_path(image_id).write_text(spec.to_json())
        return spec

    def load_spec(self, image_id: str) -> ImageSpec:
        path = self._spec_path(image_id)
        if not path.exists():
            raise NotFoundError("image not found", {"image_id": image_id})
        return ImageSpec.from_json(path.read_text())

    def open_array(self, image_id: str, writable: bool = False) -> np.memmap:
        spec = self.load_spec(image_id)
        mode = "r+" if writable else "r"
        return np.lib.format.open_memmap(spec.path, mode=mode)

    def digest_of(self, image_id: str) -> str:
        spec = self.load_spec(image_id)
        arr = np.lib.format.open_memmap(spec.path, mode="r")
        return digest_array(arr)

    def list_images(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted(self.images_dir.glob("*.json")):
            out.append(json.loads(p.read_text()))
        return out
