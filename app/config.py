"""Service configuration.

Values are read from environment variables (prefix ``GDR_``) so the same
image can run unmodified in tests and in production-like deployments.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_ENV_PREFIX = "GDR_"


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(_ENV_PREFIX + name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:  # fail fast on malformed config
        raise ValueError(f"invalid integer for {_ENV_PREFIX}{name}: {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    """Runtime limits and defaults for the reconstruction service."""

    # Maximum side length (pixels) accepted for marker/mask images.
    max_image_side: int = 4096
    # Default neighborhood connectivity (4 or 8).
    default_connectivity: int = 8
    # Default policy when the marker is not <= mask: "reject" or "clip".
    default_on_violation: str = "reject"
    # Tile side length used by the tiled engine.
    tile_size: int = 256
    # Hard cap on synchronous iterations of the reference engine; guards
    # against pathological inputs. 0 means "no explicit cap" (the iteration
    # is still guaranteed to terminate in at most max(shape) * levels steps).
    max_reference_iterations: int = 0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            max_image_side=_get_int("MAX_IMAGE_SIDE", cls.max_image_side),
            default_connectivity=_get_int(
                "DEFAULT_CONNECTIVITY", cls.default_connectivity
            ),
            default_on_violation=os.environ.get(
                _ENV_PREFIX + "DEFAULT_ON_VIOLATION", cls.default_on_violation
            ),
            tile_size=_get_int("TILE_SIZE", cls.tile_size),
            max_reference_iterations=_get_int("MAX_REFERENCE_ITERATIONS", 0),
        )


def get_settings() -> Settings:
    return Settings.from_env()
