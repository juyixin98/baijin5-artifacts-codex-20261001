"""Typed failure categories.

The service must keep four failure kinds distinguishable:

* malformed *input*                -> ``InvalidInputError``
* conflicting *state* (the theory) -> ``TheoryConflictError``
* *resource* exhaustion            -> ``ResourceLimitError``
* computational *failure*          -> ``ComputationError``

A stable machine-readable ``code`` is attached to every category so that API
responses and log lines stay filterable even if the human message changes.
"""

from __future__ import annotations

from typing import Any


class DefeasibleError(Exception):
    """Base class for every error raised deliberately by this package."""

    code: str = "internal_error"
    http_status: int = 500

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.details: dict[str, Any] = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            out["details"] = self.details
        return out


class InvalidInputError(DefeasibleError):
    """The caller supplied malformed data (parse error, bad field, ...)."""

    code = "invalid_input"
    http_status = 422


class TheoryConflictError(DefeasibleError):
    """The *theory itself* is inconsistent (e.g. a priority cycle).

    This is deliberately separate from contradictory *conclusions*: two
    opposite defaults without a comparable priority are a normal result
    (``CONFLICT``), not an error.  A priority cycle makes the theory
    ill-defined, because rule order could otherwise decide outcomes.
    """

    code = "state_conflict"
    http_status = 409


class ResourceLimitError(DefeasibleError):
    """A configured safety budget (ground rules / chains / rounds) was hit."""

    code = "resource_exhausted"
    http_status = 509


class ComputationError(DefeasibleError):
    """The engine reached a state it cannot process (genuine internal bug)."""

    code = "computation_failure"
    http_status = 500
