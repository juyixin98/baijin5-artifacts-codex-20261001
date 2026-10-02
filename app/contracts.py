"""Image data contract (API boundary).

Pydantic models enforce the schema (types, connectivity literal, positive
chunk size); semantic checks (shapes, seeds, mask consistency) live in
``collect_request_issues`` so the /validate endpoint can report every
problem at once and the segment endpoints can fail with a categorised 422.
"""

from __future__ import annotations

import base64
import binascii
import io
from typing import Literal, Optional

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

MAX_DIM = 10_000  # hard guard on a single axis; the settings-level pixel cap applies on top


class ContractIssue(BaseModel):
    category: str
    message: str
    location: str = ""


class SegmentationRequest(BaseModel):
    elevation: list[list[float]] = Field(..., description="2-D flood-level (gradient) map")
    markers: list[list[int]] = Field(..., description="2-D seed labels; 0 = unmarked, >0 = basin seed")
    connectivity: Literal[4, 8] = 8
    mask: Optional[list[list[bool]]] = None
    chunk_size: Optional[int] = Field(default=None, gt=0)


def _rectangular(rows: list[list], name: str) -> Optional[ContractIssue]:
    if not rows or not rows[0]:
        return ContractIssue(category="empty_input", message=f"{name} must be a non-empty 2-D array", location=name)
    width = len(rows[0])
    for i, row in enumerate(rows):
        if len(row) != width:
            return ContractIssue(
                category="ragged_array",
                message=f"{name} row {i} has length {len(row)}, expected {width}",
                location=f"{name}[{i}]",
            )
    if len(rows) > MAX_DIM or width > MAX_DIM:
        return ContractIssue(
            category="dimension_limit",
            message=f"{name} dimensions exceed the {MAX_DIM}-pixel axis limit",
            location=name,
        )
    return None


def collect_request_issues(req: SegmentationRequest) -> list[ContractIssue]:
    """Return every contract violation found in a request (empty = valid)."""
    issues: list[ContractIssue] = []
    for name, rows in (("elevation", req.elevation), ("markers", req.markers)):
        issue = _rectangular(rows, name)
        if issue is not None:
            issues.append(issue)
    if issues:
        return issues

    shape = (len(req.elevation), len(req.elevation[0]))
    if (len(req.markers), len(req.markers[0])) != shape:
        issues.append(
            ContractIssue(
                category="shape_mismatch",
                message=f"markers shape {(len(req.markers), len(req.markers[0]))} != elevation shape {shape}",
                location="markers",
            )
        )
        return issues

    markers = np.asarray(req.markers, dtype=np.int64)
    if int(markers.min()) < 0:
        issues.append(
            ContractIssue(category="negative_marker", message="marker labels must be >= 0", location="markers")
        )
    if not np.any(markers > 0):
        issues.append(
            ContractIssue(category="no_seeds", message="markers contain no seed (no label > 0)", location="markers")
        )

    elevation = np.asarray(req.elevation, dtype=np.float64)
    if not np.all(np.isfinite(elevation)):
        issues.append(
            ContractIssue(
                category="non_finite_elevation",
                message="elevation contains NaN or inf",
                location="elevation",
            )
        )

    if req.mask is not None:
        issue = _rectangular(req.mask, "mask")
        if issue is not None:
            issues.append(issue)
        elif (len(req.mask), len(req.mask[0])) != shape:
            issues.append(
                ContractIssue(
                    category="shape_mismatch",
                    message=f"mask shape {(len(req.mask), len(req.mask[0]))} != elevation shape {shape}",
                    location="mask",
                )
            )
        else:
            mask = np.asarray(req.mask, dtype=bool)
            if np.any((markers > 0) & ~mask):
                issues.append(
                    ContractIssue(
                        category="seed_outside_mask",
                        message="at least one seed pixel lies outside the mask",
                        location="mask",
                    )
                )
    return issues


class ImageSegmentationRequest(BaseModel):
    """PNG-based variant: elevation and markers as base64-encoded grayscale PNGs."""

    elevation_png_b64: str
    markers_png_b64: str
    connectivity: Literal[4, 8] = 8
    smooth_sigma: float = Field(default=0.0, ge=0.0)


def decode_png_gray(png_b64: str, name: str) -> np.ndarray:
    """Decode a base64 grayscale PNG into a 2-D array.

    Raises:
        ValueError: with a stable category prefix on malformed input.
    """
    try:
        raw = base64.b64decode(png_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"invalid_base64: {name} is not valid base64") from exc
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:
        raise ValueError(f"invalid_png: {name} is not a decodable PNG") from exc
    if image.mode not in ("L", "I", "I;16"):
        raise ValueError(
            f"unsupported_image_mode: {name} must be grayscale (mode L/I/I;16), got {image.mode}"
        )
    return np.asarray(image)


class ValidationReport(BaseModel):
    valid: bool
    issues: list[ContractIssue]


class SegmentationResponse(BaseModel):
    status: Literal["succeeded"] = "succeeded"
    run_id: str
    input_sha256: str
    labels: list[list[int]]
    boundary: list[list[bool]]
    stats: dict
    versions: dict[str, str]


class ErrorBody(BaseModel):
    category: str
    message: str
    run_id: Optional[str] = None


class ErrorResponse(BaseModel):
    error: ErrorBody
