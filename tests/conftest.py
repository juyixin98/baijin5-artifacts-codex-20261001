from __future__ import annotations

import base64
import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from colorconvert.config import Settings
from colorconvert.diagnostics import RequestLogger, configure_logging, new_request_id
from colorconvert.jobs import JobStore
from colorconvert.kernel import ColorKernel
from colorconvert.profiles import ProfileRegistry
from colorconvert.service import ConversionService

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="session")
def registry(settings) -> ProfileRegistry:
    return ProfileRegistry.load(settings.registry_path)


@pytest.fixture()
def kernel() -> ColorKernel:
    return ColorKernel()


@pytest.fixture()
def service(registry, settings) -> ConversionService:
    return ConversionService(registry, settings, JobStore())


@pytest.fixture()
def log() -> RequestLogger:
    return RequestLogger(configure_logging("WARNING"), new_request_id())


def make_png_b64(arr: np.ndarray, mode: str, icc: bytes | None = None) -> str:
    buf = io.BytesIO()
    kwargs = {"format": "PNG"}
    if icc is not None:
        kwargs["icc_profile"] = icc
    Image.fromarray(arr, mode=mode).save(buf, **kwargs)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def make_tiff_b64(arr: np.ndarray, mode: str, icc: bytes | None = None) -> str:
    buf = io.BytesIO()
    kwargs = {"format": "TIFF"}
    if icc is not None:
        kwargs["icc_profile"] = icc
    Image.fromarray(arr, mode=mode).save(buf, **kwargs)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def rgb_image(h: int = 8, w: int = 8) -> np.ndarray:
    """Deterministic RGB test image with varied colors."""
    rng = np.random.default_rng(42)
    return rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)


@pytest.fixture()
def png_rgb_b64() -> str:
    return make_png_b64(rgb_image(), "RGB")


@pytest.fixture()
def srgb_icc_bytes(settings) -> bytes:
    return (settings.profile_dir / "sRGB.icc").read_bytes()
