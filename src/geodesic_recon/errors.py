"""Error taxonomy.

Every client-visible failure carries a stable ``category`` string so tests and
callers can assert the *kind* of failure, not just "it raised".
"""
from __future__ import annotations


class ContractViolation(Exception):
    """Input violates the image data contract."""

    def __init__(self, category: str, detail: str):
        self.category = category
        self.detail = detail
        super().__init__(f"{category}: {detail}")


class FixedPointNotReached(Exception):
    """Kernel failed to converge within the iteration safeguard."""

    def __init__(self, detail: str):
        super().__init__(detail)
