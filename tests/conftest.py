from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from iccconv.api.app import create_app
from iccconv.config import get_settings
from iccconv.profiles.registry import ProfileRegistry

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = REPO_ROOT / "profiles"
BAD_DIR = REPO_ROOT / "tests" / "fixtures" / "bad"
GOLDEN_PATH = REPO_ROOT / "tests" / "golden" / "golden_values.json"


@pytest.fixture(scope="session")
def registry() -> ProfileRegistry:
    return ProfileRegistry(PROFILES_DIR)


@pytest.fixture(scope="session")
def golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text())


@pytest.fixture(scope="session")
def profile_bytes():
    def load(name: str) -> bytes:
        return (PROFILES_DIR / name).read_bytes()

    return load


@pytest.fixture(scope="session")
def bad_profile_bytes():
    def load(name: str) -> bytes:
        return (BAD_DIR / name).read_bytes()

    return load


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    return TestClient(create_app(get_settings()))


def encode_png(pixels: np.ndarray, icc: bytes | None = None) -> bytes:
    mode = {1: "L", 2: "LA", 3: "RGB", 4: "RGBA"}[pixels.shape[-1]]
    arr = pixels[..., 0] if pixels.shape[-1] == 1 else pixels
    img = Image.fromarray(arr, mode=mode)
    buf = io.BytesIO()
    kwargs = {"icc_profile": icc} if icc else {}
    img.save(buf, format="PNG", **kwargs)
    return buf.getvalue()


def gradient_rgb(height: int = 8, width: int = 16) -> np.ndarray:
    y, x = np.mgrid[0:height, 0:width]
    r = (x * 255 // max(width - 1, 1)).astype(np.uint8)
    g = (y * 255 // max(height - 1, 1)).astype(np.uint8)
    b = ((x + y) * 255 // max(width + height - 2, 1)).astype(np.uint8)
    return np.stack([r, g, b], axis=-1)


def alpha_ramp(height: int, width: int) -> np.ndarray:
    x = np.arange(width, dtype=np.uint16)
    a = (x * 255 // max(width - 1, 1)).astype(np.uint8)
    return np.tile(a, (height, 1))
