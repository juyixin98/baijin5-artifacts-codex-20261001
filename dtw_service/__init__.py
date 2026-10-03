"""DTW alignment service for synthetic feature sequences.

Modules
-------
contracts   : request/response schemas and failure categories (sample contracts)
constraints : fixed step pattern and Sakoe-Chiba path window
distance    : fixed local distance metric
dense       : dense dynamic-programming DTW (reference-quality core)
banded      : band-storage DTW for large matrices
backtrack   : shared optimal-path backtracking
stretch     : local stretch-rate derivation from an alignment path
stream      : streaming session state for chunked alignment
diagnostics : decision records with request ids and masked payloads
service     : validation + orchestration + decision logging
api         : FastAPI surface
"""

from dtw_service.contracts import (
    AlignmentRequest,
    AlignmentResponse,
    DecisionStatus,
    FailureCategory,
)
from dtw_service.service import align

__all__ = [
    "AlignmentRequest",
    "AlignmentResponse",
    "DecisionStatus",
    "FailureCategory",
    "align",
]
