"""Runtime version snapshot, embedded in responses and logs for reproducibility."""

from __future__ import annotations

import platform
import sys
from functools import lru_cache


@lru_cache(maxsize=1)
def version_snapshot() -> dict[str, str]:
    import fastapi
    import numpy
    import PIL
    import scipy

    from app import __version__

    return {
        "service": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy": numpy.__version__,
        "scipy": scipy.__version__,
        "pillow": PIL.__version__,
        "fastapi": fastapi.__version__,
    }
