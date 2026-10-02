"""PNG codecs for gradient and label images (Pillow).

* Gradients travel as 16-bit grayscale PNG (mode ``I;16``); decoded values
  are returned as float64 in raw digital counts (0..65535).
* Labels travel as 16-bit grayscale PNG with a fixed ``+1`` offset so the
  unreached label ``-1`` maps to 0, boundary ``0`` maps to 1, and basin
  ``k`` maps to ``k + 1``.  The offset is part of the codec contract.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

LABEL_PNG_OFFSET = 1


def decode_gradient_png(data: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(data)) as image:
        gray = image.convert("I") if image.mode != "I;16" else image
        array = np.asarray(gray, dtype=np.int32)
    if array.ndim != 2:
        raise ValueError(f"gradient PNG must be single-channel, got shape {array.shape}")
    return array.astype(np.float64)


def encode_gradient_png(gradient: np.ndarray) -> bytes:
    array = np.asarray(gradient, dtype=np.float64)
    clipped = np.clip(np.rint(array), 0, 65535).astype(np.uint16)
    buffer = io.BytesIO()
    Image.fromarray(clipped, mode="I;16").save(buffer, format="PNG")
    return buffer.getvalue()


def encode_labels_png(labels: np.ndarray) -> bytes:
    array = np.asarray(labels, dtype=np.int32) + LABEL_PNG_OFFSET
    if array.min() < 0 or array.max() > 65535:
        raise ValueError("labels out of encodable range after offset")
    buffer = io.BytesIO()
    Image.fromarray(array.astype(np.uint16), mode="I;16").save(buffer, format="PNG")
    return buffer.getvalue()


def decode_labels_png(data: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(data)) as image:
        array = np.asarray(image.convert("I"), dtype=np.int32)
    return array - LABEL_PNG_OFFSET
