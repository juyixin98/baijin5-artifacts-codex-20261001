"""Typed error taxonomy.

Every failure raised by the backend is one of these categories so that callers
(API, experiment scripts, tests) can distinguish:

* input errors          -> malformed / impossible user data (4xx class)
* state conflicts        -> run_id reuse, result recorded before started, ...
* resource exhaustion    -> memory / cell-budget guard tripped
* computation failures   -> non-finite arithmetic, non-convergence
"""

from __future__ import annotations


class AipwError(Exception):
    """Base class. ``category`` is a stable machine-readable tag."""

    category = "error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict = details or {}


class InputError(AipwError):
    category = "input_error"


class StateConflictError(AipwError):
    category = "state_conflict"


class ResourceExhaustedError(AipwError):
    category = "resource_exhausted"


class ComputationError(AipwError):
    category = "computation_failure"


CATEGORIES = (
    "input_error",
    "state_conflict",
    "resource_exhausted",
    "computation_failure",
)
