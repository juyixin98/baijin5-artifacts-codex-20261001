"""Typed errors with stable machine-readable categories.

Every failure the backend can produce carries a ``category`` string so that
API clients, job records and test assertions can match on the failure class
instead of parsing messages.  Unknown states are never mapped to success:
anything that is not a clean ``COMPLETED`` is ``FAILED`` with a category.
"""

from __future__ import annotations


class KernelError(Exception):
    """Base class for all backend errors."""

    category = "INTERNAL"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict:
        return {"category": self.category, "message": self.message}


class ContractViolation(KernelError):
    """Input rejected by the image data contract (client error)."""

    category = "CONTRACT_VIOLATION"


class ShapeMismatch(ContractViolation):
    category = "SHAPE_MISMATCH"


class EmptyMarkers(ContractViolation):
    category = "EMPTY_MARKERS"


class SeedConflict(ContractViolation):
    category = "SEED_CONFLICT"


class SeedOutOfBounds(ContractViolation):
    category = "SEED_OUT_OF_BOUNDS"


class SeedOutsideMask(ContractViolation):
    category = "SEED_OUTSIDE_MASK"


class InvalidConnectivity(ContractViolation):
    category = "INVALID_CONNECTIVITY"


class NonFiniteGradient(ContractViolation):
    category = "NON_FINITE_GRADIENT"


class ImageTooLarge(ContractViolation):
    category = "IMAGE_TOO_LARGE"


class InvalidLabel(ContractViolation):
    category = "INVALID_LABEL"


class KernelInvariantError(KernelError):
    """A kernel-internal assumption was violated (server error)."""

    category = "KERNEL_INVARIANT"
