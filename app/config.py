"""Central configuration.

Every numerical threshold that influences a pass/fail or confidence
decision lives here, overridable via environment variables prefixed with
``OPP495_`` so runs are reproducible without code edits.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_ENV_PREFIX = "OPP495_"


def _env(name: str, cast, default):
    raw = os.environ.get(_ENV_PREFIX + name)
    if raw is None:
        return default
    return cast(raw)


@dataclass(frozen=True)
class KernelConfig:
    """Thresholds for the phase-correlation kernel and confidence policy."""

    window: str = "hann"  # "hann" | "none"; declared, never implicit
    pad_factor: int = 2  # zero-padding factor -> linear (non-wrapping) range
    # Estimation floor: bins <= eps_ratio*max are zeroed. ~1e-12 keeps every
    # numerically meaningful bin -> sharpest peak, best subpixel accuracy.
    eps_ratio: float = 1e-12
    # Ambiguity floor: a stricter floor keeps only true spectral lines, so
    # periodic textures keep their lattice replica peaks (used solely for
    # ambiguity detection, never for the shift estimate itself).
    ambiguity_eps_ratio: float = 1e-3
    # Above this fraction of zeroed bins the spectrum is unusable (a
    # constant image zeroes 100% of bins at the estimation floor).
    max_degenerate_fraction: float = 0.98
    peak_count: int = 5  # top-K local maxima reported for ambiguity checks
    psr_exclusion_radius: int = 5  # px disk around peak excluded from PSR stats
    psr_failure: float = 3.0  # below -> hard failure LOW_PSR
    psr_ok: float = 6.0  # below -> uncertainty LOW_PSR
    ambiguity_ratio: float = 0.8  # second/main peak ratio -> AMBIGUOUS_PEAKS
    min_overlap: float = 0.5  # below -> uncertainty INSUFFICIENT_OVERLAP
    hard_min_overlap: float = 0.1  # below -> failure INSUFFICIENT_OVERLAP
    confidence_ok: float = 0.5  # below -> status cannot be "ok"

    @classmethod
    def from_env(cls) -> "KernelConfig":
        return cls(
            window=_env("WINDOW", str, cls.window),
            pad_factor=_env("PAD_FACTOR", int, cls.pad_factor),
            eps_ratio=_env("EPS_RATIO", float, cls.eps_ratio),
            ambiguity_eps_ratio=_env(
                "AMBIGUITY_EPS_RATIO", float, cls.ambiguity_eps_ratio
            ),
            max_degenerate_fraction=_env(
                "MAX_DEGENERATE_FRACTION", float, cls.max_degenerate_fraction
            ),
            peak_count=_env("PEAK_COUNT", int, cls.peak_count),
            psr_exclusion_radius=_env(
                "PSR_EXCLUSION_RADIUS", int, cls.psr_exclusion_radius
            ),
            psr_failure=_env("PSR_FAILURE", float, cls.psr_failure),
            psr_ok=_env("PSR_OK", float, cls.psr_ok),
            ambiguity_ratio=_env("AMBIGUITY_RATIO", float, cls.ambiguity_ratio),
            min_overlap=_env("MIN_OVERLAP", float, cls.min_overlap),
            hard_min_overlap=_env("HARD_MIN_OVERLAP", float, cls.hard_min_overlap),
            confidence_ok=_env("CONFIDENCE_OK", float, cls.confidence_ok),
        )


@dataclass(frozen=True)
class Settings:
    """Service-level settings (paths, payload limits, tiling defaults)."""

    fixtures_dir: Path = field(
        default_factory=lambda: _env(
            "FIXTURES_DIR", Path, REPO_ROOT / "fixtures"
        )
    )
    reports_dir: Path = field(
        default_factory=lambda: _env("REPORTS_DIR", Path, REPO_ROOT / "reports")
    )
    min_image_dim: int = 16
    max_image_dim: int = 1024
    tile_size: int = 64
    tile_halo: int = 16
    kernel: KernelConfig = field(default_factory=KernelConfig.from_env)


def get_settings() -> Settings:
    return Settings()
