"""Independent configuration for the seam-carving backend.

Configuration is a frozen (immutable) dataclass. It can be built from
environment variables (prefix ``SEAMCARVE_``) or from a plain dict, and
validates itself on construction.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

VALID_ENERGY_MODES = ("gradient", "forward")

_ENV_PREFIX = "SEAMCARVE_"


@dataclass(frozen=True)
class SeamConfig:
    """Runtime knobs. The DP displacement limit is NOT here: it is a
    documented algorithmic constant (``kernel.MAX_STEP``), not a knob."""

    energy_mode: str = "gradient"   # default energy: "gradient" | "forward"
    chunk_size: int = 4             # seams per progress chunk in jobs
    min_width: int = 1              # smallest image width we carve down to
    log_dir: str = "artifacts"      # where run logs are written

    def __post_init__(self) -> None:
        if self.energy_mode not in VALID_ENERGY_MODES:
            raise ValueError(
                f"energy_mode must be one of {VALID_ENERGY_MODES}, "
                f"got {self.energy_mode!r}"
            )
        if self.chunk_size < 1:
            raise ValueError(f"chunk_size must be >= 1, got {self.chunk_size}")
        if self.min_width < 1:
            raise ValueError(f"min_width must be >= 1, got {self.min_width}")

    @classmethod
    def from_dict(cls, values: dict) -> "SeamConfig":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        unknown = set(values) - known
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        return cls(**values)

    @classmethod
    def from_env(cls) -> "SeamConfig":
        values: dict = {}
        env = os.environ
        if _ENV_PREFIX + "ENERGY_MODE" in env:
            values["energy_mode"] = env[_ENV_PREFIX + "ENERGY_MODE"]
        if _ENV_PREFIX + "CHUNK_SIZE" in env:
            values["chunk_size"] = int(env[_ENV_PREFIX + "CHUNK_SIZE"])
        if _ENV_PREFIX + "MIN_WIDTH" in env:
            values["min_width"] = int(env[_ENV_PREFIX + "MIN_WIDTH"])
        if _ENV_PREFIX + "LOG_DIR" in env:
            values["log_dir"] = env[_ENV_PREFIX + "LOG_DIR"]
        return cls(**values)
