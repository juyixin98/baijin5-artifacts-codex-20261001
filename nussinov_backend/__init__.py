"""Nussinov maximum-base-pairing backend (teaching combinatorial model).

Modules:
    parser: synthetic RNA sequence normalization and validation
    domain: Nussinov DP, traceback, dot-bracket rendering, legality checks
    trace:  request-scoped lineage / provenance backed by SQLite
    api:    FastAPI validation interface
"""

__version__ = "1.0.0"

ALGORITHM_NAME = "nussinov"
ALGORITHM_VERSION = "1.0.0"
MODEL_SCOPE = "teaching-combinatorial"
