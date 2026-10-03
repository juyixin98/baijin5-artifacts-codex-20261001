"""Domain error taxonomy.

Failure *categories* are part of the service contract: callers (and tests)
match on ``code`` rather than on message text.
"""

from __future__ import annotations


class MappingError(Exception):
    """Base class for all mapping failures.

    Attributes:
        code: stable machine-readable failure category.
        detail: human-readable explanation.
        key_state: relevant interval/position values for diagnostics.
    """

    code: str = "mapping_error"

    def __init__(self, detail: str, **key_state: object) -> None:
        super().__init__(detail)
        self.detail = detail
        self.key_state = dict(key_state)

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "detail": self.detail, "key_state": self.key_state}


class TranscriptNotFoundError(MappingError):
    """Transcript id is not present in the data source."""

    code = "transcript_not_found"


class InvalidIntervalError(MappingError):
    """Interval is malformed (negative, start > end, wrong ordering)."""

    code = "invalid_interval"


class CoordinateOutOfRangeError(MappingError):
    """Position/interval lies outside the domain it is addressed in."""

    code = "coordinate_out_of_range"


class IntronicPositionError(MappingError):
    """A genomic coordinate falls in an intron and must NOT be hard-mapped."""

    code = "intronic_position"


class RegionNotMappableError(MappingError):
    """A region overlaps intronic/intergenic space and cannot map as one block."""

    code = "region_not_mappable"


class ValidationError(MappingError):
    """Fixture/data integrity validation failure."""

    code = "validation_error"
