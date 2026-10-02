"""Package and runtime dependency versions, surfaced in logs and the API."""

from __future__ import annotations

import platform

import fastapi
import numpy
import PIL
import scipy

__version__ = "0.1.0"


def runtime_versions() -> dict[str, str]:
    """Versions of every component that can influence numerical output."""
    return {
        "python": platform.python_version(),
        "watershed_backend": __version__,
        "numpy": numpy.__version__,
        "scipy": scipy.__version__,
        "pillow": PIL.__version__,
        "fastapi": fastapi.__version__,
    }
