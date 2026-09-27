"""Unit tests for the rule language: parsing and theory validation."""

from __future__ import annotations

import pytest

from defeasible.errors import InvalidInputError, TheoryConflictError
from defeasible.language import (
    RuleKind,
    Term,
    Theory,
    priority_rank,
    validate_theory,
)


class TestTermParsing:
    def test_positive_atom_with_args(self) -> None:
        t = Term.parse("bird(tweety)")
        assert t.predicate == "bird"
        assert t.args == ("tweety",)
        assert not t.negated
        assert t.is_ground

    def test_negated_literal_and_opposite(self) -> None:
        t = Term.parse("-flies(X)")
        assert t.negated
        assert t.variables == frozenset({"X"})
        assert t.opposite.literal == "flies(X)"
        assert t.opposite.opposite == t

    def test_zero_arity(self) -> None:
        assert Term.parse("closed").args == ()

    @pytest.mark.parametrize(
        "text",
        ["-", "123pred(x)", "Pred(x,)", "p(9x)", "", "p(x y)"],
    )
    def test_malformed_literals_raise(self, text: str) -> None:
        with pytest.raises(InvalidInputError):
            Term.parse(text)

    def test_variable_case_rule(self) -> None:
        # upper-case => variable; lower-case => constant
        assert Term.parse("p(X)").is_ground is False
        assert Term.parse("p(x)").is_ground is True


class TestTheoryValidation:
    def _two_defaults(self) -> Theory:
        t = Theory()
        t.add_rule("a", "default", ["p(X)"], "q(X)")
        t.add_rule("b", "default", ["r(X)"], "-q(X)")
        return t

    def test_duplicate_rule_id_rejected(self) -> None:
        t = Theory()
        t.add_rule("a", "default", ["p(X)"], "q(X)")
        t.add_rule("a", "default", ["r(X)"], "s(X)")
        with pytest.raises(InvalidInputError, match="duplicate"):
            validate_theory(t)

    def test_unbound_head_variable_rejected(self) -> None:
        t = Theory()
        t.add_rule("a", "default", ["p(X)"], "q(Y)")
        with pytest.raises(InvalidInputError, match="range-restricted"):
            validate_theory(t)

    def test_variable_only_in_negated_body_rejected(self) -> None:
        t = Theory()
        t.add_rule("a", "default", ["p(X)", "-r(Y)"], "q(X)")
        with pytest.raises(InvalidInputError):
            validate_theory(t)

    def test_priority_must_reference_existing_rules(self) -> None:
        t = self._two_defaults()
        t.add_priority("a", "ghost")
        with pytest.raises(InvalidInputError, match="unknown rule"):
            validate_theory(t)

    def test_priority_between_defaults_only(self) -> None:
        t = Theory()
        t.add_rule("d", "default", ["p(X)"], "q(X)")
        t.add_rule("s", "strict", ["p(X)"], "-q(X)")
        t.add_priority("d", "s")
        with pytest.raises(InvalidInputError, match="default"):
            validate_theory(t)

    def test_priority_self_cycle_rejected(self) -> None:
        t = self._two_defaults()
        t.add_priority("a", "b")
        t.add_priority("b", "a")
        with pytest.raises(TheoryConflictError) as exc:
            validate_theory(t)
        assert exc.value.code == "state_conflict"
        cycle = exc.value.details["cycle"]
        assert cycle[0] == cycle[-1]  # reports a closed cycle

    def test_three_node_cycle_reported(self) -> None:
        t = Theory()
        for rid, body, head in [
            ("a", "p(X)", "q(X)"),
            ("b", "r(X)", "-q(X)"),
            ("c", "s(X)", "q(X)"),
        ]:
            t.add_rule(rid, "default", [body], head)
        t.add_priority("a", "b")
        t.add_priority("b", "c")
        t.add_priority("c", "a")
        with pytest.raises(TheoryConflictError):
            validate_theory(t)

    def test_valid_acyclic_theory_accepted(self) -> None:
        t = self._two_defaults()
        t.add_priority("a", "b")
        validate_theory(t)  # no exception
        ranks = priority_rank(t)
        assert ranks["a"] > ranks["b"]

    def test_round_trip_serialisation(self) -> None:
        t = self._two_defaults()
        t.add_priority("a", "b")
        again = Theory.from_dict(t.to_dict())
        assert again.to_dict() == t.to_dict()
        assert again.rules[0].kind is RuleKind.DEFAULT
