"""Typed error categories.

Every failure the pipeline can produce maps to one of these categories so
callers (and tests) can assert on the *kind* of failure instead of parsing
message strings. The API layer translates them to HTTP statuses; nothing is
silently converted into a success response.
"""

from __future__ import annotations


class MsaBackendError(Exception):
    """Base class; ``category`` is a stable machine-readable slug."""

    category = "internal_error"


class FastaParseError(MsaBackendError):
    """Input text is not well-formed FASTA."""

    category = "fasta_parse_error"


class AlignmentShapeError(MsaBackendError):
    """Sequences do not form a rectangular alignment."""

    category = "alignment_shape_error"


class InvalidResidueError(MsaBackendError):
    """A symbol outside the supported IUPAC DNA alphabet was found."""

    category = "invalid_residue_error"


class InsufficientDataError(MsaBackendError):
    """The alignment is too small to compute anything meaningful."""

    category = "insufficient_data_error"


class RunNotFoundError(MsaBackendError):
    """Requested run id is absent from the provenance store."""

    category = "run_not_found"
