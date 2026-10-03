"""Numerical kernel: phase correlation, subpixel refinement, reference."""

from app.kernel.phasecorr import PhaseCorrSurface, Peak, cross_power_surface
from app.kernel.pipeline import EstimateResult, estimate_shift
from app.kernel.reference import ReferenceResult, ncc_reference
from app.kernel.subpixel import SubpixelResult, estimate_subpixel, parabolic_extremum

__all__ = [
    "EstimateResult",
    "PhaseCorrSurface",
    "Peak",
    "ReferenceResult",
    "SubpixelResult",
    "cross_power_surface",
    "estimate_shift",
    "estimate_subpixel",
    "ncc_reference",
    "parabolic_extremum",
]
