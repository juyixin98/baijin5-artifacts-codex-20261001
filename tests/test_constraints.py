"""Constraint validation: conflicts are detected BEFORE clustering and carry
a distinguishable failure category."""

import pytest

from er_backend.errors import (
    ConstraintConflictError,
    ERCategory,
    InputValidationError,
)
from er_backend.kernel.constraints import (
    must_link_components,
    validate_constraints,
)
from er_backend.models import ConstraintSet

KNOWN = {"A", "B", "C", "D"}


def test_unknown_record_id_is_input_error():
    constraints = ConstraintSet(must_link=[("A", "ZZZ")])
    with pytest.raises(InputValidationError) as excinfo:
        validate_constraints(constraints, KNOWN)
    assert excinfo.value.category == ERCategory.INPUT_ERROR
    assert "ZZZ" in str(excinfo.value.details)


def test_transitive_must_link_chain_contradicting_cannot_link_is_conflict():
    # A-B and B-C must-link transitively force A and C together; the
    # cannot-link A-C must be rejected as a state conflict.
    constraints = ConstraintSet(
        must_link=[("A", "B"), ("B", "C")], cannot_link=[("A", "C")]
    )
    with pytest.raises(ConstraintConflictError) as excinfo:
        validate_constraints(constraints, KNOWN)
    assert excinfo.value.category == ERCategory.STATE_CONFLICT
    assert excinfo.value.details["cannot_link"] == ["A", "C"]


def test_direct_must_cannot_contradiction_is_conflict():
    constraints = ConstraintSet(must_link=[("A", "B")], cannot_link=[("B", "A")])
    with pytest.raises(ConstraintConflictError):
        validate_constraints(constraints, KNOWN)


def test_self_cannot_link_is_conflict():
    constraints = ConstraintSet(cannot_link=[("A", "A")])
    with pytest.raises(ConstraintConflictError):
        validate_constraints(constraints, KNOWN)


def test_consistent_constraints_pass_and_components_are_transitive():
    constraints = ConstraintSet(
        must_link=[("A", "B"), ("B", "C")], cannot_link=[("C", "D")]
    )
    validate_constraints(constraints, KNOWN)  # must not raise
    comps = must_link_components(sorted(KNOWN), constraints)
    assert {frozenset(c) for c in comps} == {
        frozenset({"A", "B", "C"}),
        frozenset({"D"}),
    }
