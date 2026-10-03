"""Domain failure categories.

Every expected failure maps to a stable category string so API clients and
tests can assert on the *kind* of failure, not just "the call failed".
"""

from __future__ import annotations

from enum import Enum


class FailureCategory(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"          # malformed variants/reads
    NO_OBSERVATIONS = "NO_OBSERVATIONS"      # nothing usable to phase
    BLOCK_TOO_LARGE = "BLOCK_TOO_LARGE"      # block exceeds exact-enumeration limit


class PhasingError(Exception):
    """Domain error carrying a machine-readable failure category."""

    def __init__(self, category: FailureCategory, detail: str) -> None:
        super().__init__(f"{category.value}: {detail}")
        self.category = category
        self.detail = detail
