"""Pure gain-math primitives (no streaming state lives here)."""

from __future__ import annotations


def required_gain(peak: float, threshold: float) -> float:
    """Instantaneous gain needed to bring ``peak`` under ``threshold``."""
    if peak <= threshold:
        return 1.0
    return threshold / peak


def smooth_step(g_prev: float, target: float, attack_coeff: float, release_coeff: float) -> float:
    """One-pole smoother step: attack when the target demands less gain."""
    c = attack_coeff if target < g_prev else release_coeff
    return c * g_prev + (1.0 - c) * target
