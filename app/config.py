"""Runtime configuration.

Defaults are fixed and deterministic (window, order, numerical thresholds).
Any value may be overridden through environment variables prefixed with
``LPC_`` so deployments can tune the service without code changes, but the
test-suite and the fixtures always run against the defaults below.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

# Numerical thresholds (fixed by the algorithm contract, not per-deployment).
ZERO_ENERGY_EPS = 1e-12  # r[0] below this -> zero-energy frame
ERROR_ENERGY_EPS = 1e-14  # prediction-error energy collapse -> singular system
REFLECTION_STABILITY_TOL = 1e-9  # |k| >= 1 - tol -> unstable reflection coefficient
MARGINAL_REFLECTION = 0.999  # |k| above this (but stable) -> uncertainty warning
POLE_STABILITY_TOL = 1e-6  # max |pole| may exceed 1 by at most this
RECONSTRUCTION_TOL = 1e-9  # relative reconstruction error tolerance
TOEPLITZ_CROSSCHECK_TOL = 1e-6  # max |a_levinson - a_toeplitz| tolerated

SUPPORTED_WINDOWS = ("hann", "hamming", "rect")


@dataclass(frozen=True)
class Settings:
    sample_rate: int = 16000
    default_order: int = 10
    default_window: str = "hann"
    max_frame_size: int = 4096
    max_order: int = 64
    zero_energy_eps: float = ZERO_ENERGY_EPS
    error_energy_eps: float = ERROR_ENERGY_EPS
    reflection_tol: float = REFLECTION_STABILITY_TOL
    marginal_reflection: float = MARGINAL_REFLECTION
    pole_tol: float = POLE_STABILITY_TOL
    reconstruction_tol: float = RECONSTRUCTION_TOL
    toeplitz_tol: float = TOEPLITZ_CROSSCHECK_TOL


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    return Settings(
        sample_rate=int(env.get("LPC_SAMPLE_RATE", "16000")),
        default_order=int(env.get("LPC_DEFAULT_ORDER", "10")),
        default_window=env.get("LPC_DEFAULT_WINDOW", "hann"),
        max_frame_size=int(env.get("LPC_MAX_FRAME_SIZE", "4096")),
        max_order=int(env.get("LPC_MAX_ORDER", "64")),
    )
