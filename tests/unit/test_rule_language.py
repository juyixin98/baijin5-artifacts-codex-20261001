"""规则语言: 词法/语法的具体断言。"""
from __future__ import annotations

import pytest

from app.errors import RuleLanguageError
from app.rulelang import parse_fact, parse_literal, parse_query, parse_theory


def test_fact_is_parsed_as_ground_literal():
    theory = parse_theory("Bird(tweety).")
    assert len(theory.facts) == 1
    fact = theory.facts[0]
    assert fact.predicate == "Bird"
    assert fact.is_ground()
    assert fact.args == (("c", "tweety"),)
    assert not fact.negated


def test_uppercase_identifier_is_variable_lowercase_is_constant():
    lit = parse_literal("Flies(X, tweety)")
    assert lit.args[0] == ("v", "X")
    assert lit.args[1] == ("c", "tweety")


def test_string_and_integer_constants():
    lit = parse_literal('Color(sky, "blue", 3)')
    assert lit.args[1] == ("c", "blue")
    assert lit.args[2] == ("c", 3)


def test_strong_negation_and_naf_flags():
    lit = parse_literal("not -Flies(X)")
    assert lit.naf is True
    assert lit.negated is True


def test_strict_and_defeasible_rules_are_separated():
    theory = parse_theory(
        """
        A.
        @s B :- A.
        @d C := A.
        """
    )
    assert [r.rule_id for r in theory.strict_rules] == ["s"]
    assert [r.rule_id for r in theory.defeasible_rules] == ["d"]
    assert theory.all_rules[0].is_strict


def test_rule_render_roundtrip_contains_arrow():
    theory = parse_theory("@r1 Flies(X) := Bird(X).")
    text = theory.defeasible_rules[0].render()
    assert ":=" in text
    assert "@r1" in text
    # 重新解析渲染结果得到相同结构
    again = parse_theory(text)
    assert again.defeasible_rules[0].rule_id == "r1"
    assert again.defeasible_rules[0].head.predicate == "Flies"


def test_comments_are_ignored():
    theory = parse_theory(
        """
        % 行注释
        Bird(t).  # 井号注释
        """
    )
    assert len(theory.facts) == 1


def test_parse_fact_accepts_optional_trailing_dot():
    assert parse_fact("Bird(t).").predicate == "Bird"
    assert parse_fact("Bird(t)").predicate == "Bird"


@pytest.mark.parametrize(
    "text,fragment",
    [
        ("Bird(tweety", "期望符号"),
        ("@r Flies(X) := Bird(X)", "期望符号"),
        ("@r Flies(X) :- Bird(X), .", "期望标识符"),
        ('Bird("unclosed)', "引号"),
        ("Bird($x).", "无法识别"),
        ("@r not Flies(X) := Bird(X).", "规则头"),
    ],
)
def test_syntax_errors_are_input_errors(text, fragment):
    with pytest.raises(RuleLanguageError) as exc:
        parse_theory(text)
    assert exc.value.category.value == "input_error"
    assert fragment in exc.value.message
    assert "line" in exc.value.details or "token" in exc.value.details


def test_duplicate_rule_id_rejected():
    with pytest.raises(RuleLanguageError):
        parse_theory("A. @a B := A. @a C := A.")


def test_query_cannot_use_naf():
    with pytest.raises(RuleLanguageError):
        parse_query("not Flies(tweety)")
