"""Shared status / failure taxonomy used by kernel, jobs and API layers."""
from __future__ import annotations

from enum import Enum


class EstimateStatus(str, Enum):
    OK = "ok"                  # shift estimated and verified
    UNCERTAIN = "uncertain"    # shift reported but with explicit caveats
    FAILED = "failed"          # no trustworthy shift could be produced


class FailureCategory(str, Enum):
    INVALID_IMAGE = "invalid_image"            # bad shape / dtype / non-finite
    SHAPE_MISMATCH = "shape_mismatch"          # ref/mov shapes differ
    FLAT_RESPONSE = "flat_response"            # constant image, no phase info
    PEAK_AT_BORDER = "peak_at_border"          # shift at edge of unambiguous range
    NO_COMMON_CONTENT = "no_common_content"    # overlap NCC too low


class Uncertainty(str, Enum):
    AMBIGUOUS_PEAKS = "ambiguous_peaks"        # periodic texture: several peaks
    LOW_OVERLAP = "low_overlap"                # overlap fraction below threshold
    LOW_CONFIDENCE = "low_confidence"          # weak peak statistics
    BRIGHTNESS_CHANGE = "brightness_change"    # gain/offset detected (not a failure)
