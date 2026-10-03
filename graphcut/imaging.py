"""Image data contract: raw payloads -> validated unary terms.

Supported inputs (all local, no external services):

- ``array``: nested JSON lists of grayscale intensities in [0, 255];
- ``png_base64``: a base64-encoded grayscale PNG (decoded with Pillow).

Intensities are converted to data terms with the ``intensity_quadratic``
model:  D_p(label) = ((I_p - mean_label) / sigma)^2, which is non-negative
by construction.  Callers may also bypass the image entirely and supply
explicit unary arrays (validated non-negative in ``graphcut.contracts``).
"""

from __future__ import annotations

import base64
import binascii
import io
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .errors import InputValidationError

_MAX_BASE64_CHARS = 40_000_000  # ~30 MB decoded; guards against absurd payloads


@dataclass(frozen=True)
class IntensityQuadraticModel:
    fg_mean: float
    bg_mean: float
    sigma: float

    def unaries(self, intensity: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if self.sigma <= 0:
            raise InputValidationError(
                f"data model sigma must be positive, got {self.sigma!r}",
                code="DATA_MODEL_INVALID",
                details={"sigma": self.sigma},
            )
        z = intensity.astype(np.float64) / self.sigma
        unary1 = (z - self.fg_mean / self.sigma) ** 2
        unary0 = (z - self.bg_mean / self.sigma) ** 2
        return unary0, unary1


def decode_array_image(data: object) -> np.ndarray:
    arr = np.asarray(data, dtype=np.float64)
    if arr.ndim != 2:
        raise InputValidationError(
            f"array image must be 2-D grayscale, got ndim={arr.ndim}",
            code="IMAGE_FORMAT_INVALID",
            details={"ndim": int(arr.ndim)},
        )
    if not np.all(np.isfinite(arr)):
        raise InputValidationError(
            "array image contains non-finite values",
            code="IMAGE_FORMAT_INVALID",
        )
    if arr.size and (float(arr.min()) < 0.0 or float(arr.max()) > 255.0):
        raise InputValidationError(
            "array image intensities must lie in [0, 255]",
            code="IMAGE_RANGE_INVALID",
            details={"min": float(arr.min()), "max": float(arr.max())},
        )
    return arr


def decode_png_base64(payload: str) -> np.ndarray:
    if len(payload) > _MAX_BASE64_CHARS:
        raise InputValidationError(
            "base64 image payload too large",
            code="IMAGE_TOO_LARGE_PAYLOAD",
            details={"chars": len(payload), "limit": _MAX_BASE64_CHARS},
        )
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InputValidationError(
            f"invalid base64 image payload: {exc}",
            code="IMAGE_DECODE_FAILED",
        ) from exc
    try:
        with Image.open(io.BytesIO(raw)) as img:
            gray = img.convert("L")
            arr = np.asarray(gray, dtype=np.float64)
    except Exception as exc:
        raise InputValidationError(
            f"cannot decode PNG image: {exc}",
            code="IMAGE_DECODE_FAILED",
            details={"exception": type(exc).__name__},
        ) from exc
    return arr
