"""DTW service: dynamic time warping of synthetic feature sequences.

Modules:
    contracts   -- request/response schemas and status vocabulary (sample contracts)
    constraints -- fixed step pattern, slope constraint, Sakoe-Chiba window
    core        -- banded/dense DP accumulator and backtracking (signal algorithm)
    stretch     -- local stretch-rate estimation from a warping path
    stream      -- streaming alignment state over sliding buffers
    diagnostics -- request-scoped decision logging with masked inputs
    settings    -- YAML-backed configuration
    api         -- FastAPI surface
"""

from dtw_service.core import DtwResult, dtw_align
from dtw_service.stretch import local_stretch_rates

__all__ = ["DtwResult", "dtw_align", "local_stretch_rates"]
