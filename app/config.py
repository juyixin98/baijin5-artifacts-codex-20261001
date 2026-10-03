"""Independent service configuration.

Values are read from environment variables prefixed with ``SEAMCARVE_`` and
validated at construction time.  Every consumer receives an explicit
``Settings`` object; nothing reads module-level mutable state.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.errors import InvalidConfigError

ENERGY_GRADIENT = "gradient"
ENERGY_FORWARD = "forward"
VALID_ENERGY_MODES = (ENERGY_GRADIENT, ENERGY_FORWARD)

# Forward energy (Avidan & Shamir 2007) is defined for the 3-neighbour
# recurrence only, i.e. adjacent displacement exactly 1.
FORWARD_ENERGY_DISPLACEMENT = 1


@dataclass(frozen=True)
class Settings:
    energy_mode: str = ENERGY_GRADIENT
    max_displacement: int = 1
    job_chunk_size: int = 4
    log_level: str = "INFO"
    min_remaining_width: int = 1

    def validate(self) -> "Settings":
        if self.energy_mode not in VALID_ENERGY_MODES:
            raise InvalidConfigError(
                f"unknown energy_mode {self.energy_mode!r}",
                details={"valid_modes": list(VALID_ENERGY_MODES)},
            )
        if self.max_displacement < 1:
            raise InvalidConfigError(
                "max_displacement must be >= 1",
                details={"max_displacement": self.max_displacement},
            )
        if (
            self.energy_mode == ENERGY_FORWARD
            and self.max_displacement != FORWARD_ENERGY_DISPLACEMENT
        ):
            raise InvalidConfigError(
                "forward energy requires max_displacement == 1",
                details={
                    "energy_mode": self.energy_mode,
                    "max_displacement": self.max_displacement,
                },
            )
        if self.job_chunk_size < 1:
            raise InvalidConfigError(
                "job_chunk_size must be >= 1",
                details={"job_chunk_size": self.job_chunk_size},
            )
        if self.min_remaining_width < 1:
            raise InvalidConfigError(
                "min_remaining_width must be >= 1",
                details={"min_remaining_width": self.min_remaining_width},
            )
        return self


def load_settings(env: dict[str, str] | None = None) -> Settings:
    source = os.environ if env is None else env
    settings = Settings(
        energy_mode=source.get("SEAMCARVE_ENERGY_MODE", ENERGY_GRADIENT),
        max_displacement=int(source.get("SEAMCARVE_MAX_DISPLACEMENT", "1")),
        job_chunk_size=int(source.get("SEAMCARVE_JOB_CHUNK_SIZE", "4")),
        log_level=source.get("SEAMCARVE_LOG_LEVEL", "INFO"),
        min_remaining_width=int(source.get("SEAMCARVE_MIN_REMAINING_WIDTH", "1")),
    )
    return settings.validate()
