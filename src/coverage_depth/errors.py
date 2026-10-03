"""Typed errors and record-level reason codes.

Record-level problems never raise through the API; they are captured as
Decision objects. The exceptions here are for request-level and
programmer-level failures only.
"""

from __future__ import annotations

import enum


class ReasonCode(str, enum.Enum):
    """Stable machine-readable codes for per-record decisions."""

    ACCEPTED = "ACCEPTED"
    PARSE_ERROR = "PARSE_ERROR"          # TSV line could not be parsed at all
    CIGAR_ERROR = "CIGAR_ERROR"          # CIGAR string malformed
    EMPTY_INTERVAL = "EMPTY_INTERVAL"    # end <= start after CIGAR walk
    OUT_OF_BOUNDS = "OUT_OF_BOUNDS"      # block falls outside [0, ref_length)
    UNMAPPED = "UNMAPPED"                # flag 0x4 set
    SECONDARY = "SECONDARY"              # flag 0x100 set
    SUPPLEMENTARY = "SUPPLEMENTARY"      # flag 0x800 set
    QC_FAIL = "QC_FAIL"                  # flag 0x200 set
    DUPLICATE = "DUPLICATE"              # flag 0x400 set
    LOW_MAPQ = "LOW_MAPQ"                # mapq < min_mapq
    MAPQ_UNKNOWN = "MAPQ_UNKNOWN"        # mapq missing -> cannot decide quality


class DecisionStatus(str, enum.Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNDECIDABLE = "UNDECIDABLE"


class CoverageError(Exception):
    """Base class for request-level failures surfaced by the API."""


class ReferenceError(CoverageError):
    """Unknown or inconsistent reference declaration."""


class InputTooLargeError(CoverageError):
    """Request exceeded a configured safety limit."""
