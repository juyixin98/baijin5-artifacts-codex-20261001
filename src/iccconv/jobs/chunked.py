"""Chunked conversion jobs.

Large images are converted tile by tile.  Each tile is converted as its
own document through the same conversion function, so a chunked result is
bit-identical to a whole-image conversion (the engine transform is
per-pixel).  The job record carries per-tile digests so partial results
can be audited without storing pixels.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Callable, Iterator, Protocol

import numpy as np

from ..contract.image import ImageDocument
from ..errors import ContractViolationError


class ConversionFn(Protocol):
    def __call__(self, doc: ImageDocument) -> "ConversionResultLike": ...


class ConversionResultLike(Protocol):
    document: ImageDocument


@dataclass(frozen=True)
class TileRecord:
    index: int
    y: int
    x: int
    height: int
    width: int
    sha256: str


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    status: str
    tile_size: int
    tiles: tuple[TileRecord, ...]
    result_sha256: str


def tile_slices(height: int, width: int, tile_size: int) -> Iterator[tuple[slice, slice]]:
    for y in range(0, height, tile_size):
        for x in range(0, width, tile_size):
            yield slice(y, min(y + tile_size, height)), slice(x, min(x + tile_size, width))


def _digest(pixels: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(pixels).tobytes()).hexdigest()


def convert_chunked(
    doc: ImageDocument,
    *,
    tile_size: int,
    convert_fn: Callable[[ImageDocument], ConversionResultLike],
) -> tuple[ImageDocument, JobRecord]:
    """Convert a document in tiles; returns (result_document, job_record)."""
    if tile_size <= 0:
        raise ContractViolationError("tile_size must be a positive integer")

    out: np.ndarray | None = None
    out_color_space = None
    out_alpha_mode = None
    records: list[TileRecord] = []

    for index, (ys, xs) in enumerate(tile_slices(doc.height, doc.width, tile_size)):
        tile = ImageDocument(
            pixels=np.ascontiguousarray(doc.pixels[ys, xs, :]),
            color_space=doc.color_space,
            alpha_mode=doc.alpha_mode,
            embedded_profile=doc.embedded_profile,
        )
        result = convert_fn(tile).document
        if out is None:
            out_color_space = result.color_space
            out_alpha_mode = result.alpha_mode
            out = np.empty(
                (doc.height, doc.width, result.channels), dtype=np.uint8
            )
        assert out is not None
        out[ys, xs, :] = result.pixels
        records.append(
            TileRecord(
                index=index,
                y=ys.start,
                x=xs.start,
                height=ys.stop - ys.start,
                width=xs.stop - xs.start,
                sha256=_digest(result.pixels),
            )
        )

    if out is None or out_color_space is None or out_alpha_mode is None:
        raise ContractViolationError("document has no tiles to convert")

    result_doc = ImageDocument(
        pixels=out,
        color_space=out_color_space,
        alpha_mode=out_alpha_mode,
        embedded_profile=doc.embedded_profile,
    )
    job = JobRecord(
        job_id=uuid.uuid4().hex,
        status="completed",
        tile_size=tile_size,
        tiles=tuple(records),
        result_sha256=_digest(out),
    )
    return result_doc, job
