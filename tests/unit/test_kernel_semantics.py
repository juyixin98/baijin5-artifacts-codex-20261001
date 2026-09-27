"""内核语义: 以手工编写的期望结果驱动, 断言具体状态/理由码/链内容。

期望来自 tests/fixtures/expected_results.yaml (人工编写, 非被测核心生成),
并在 test_kernel_vs_oracle 中与一份独立预言机的结论交叉核验。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.kernel.facade import answer_query, answer_to_dict, compile_knowledge_base
from app.rulelang import parse_query, parse_theory
from tests.conftest import FIXTURES, SAMPLES

with open(FIXTURES / "expected_results.yaml", "r", encoding="utf-8") as _fh:
    _SPEC = yaml.safe_load(_fh)
_SINGLE_CASES = [c for c in _SPEC["cases"] if "expect" in c]
_ENUM_CASES = [c for c in _SPEC["cases"] if "expect_answers" in c]
_ERROR_CASES = _SPEC["error_cases"]


def _theory_from_case(case):
    if "theory_inline" in case:
        return parse_theory(case["theory_inline"])
    return parse_theory(
        Path(SAMPLES / case["theory_file"]).read_text(encoding="utf-8")
    )


def _compile(case, limits):
    theory = _theory_from_case(case)
    return compile_knowledge_base(
        theory.facts, theory.all_rules, theory.priorities, limits
    )


def _single_answer(case, limits):
    compiled = _compile(case, limits)
    answers = [
        answer_to_dict(a)
        for a in answer_query(
            compiled,
            parse_query(case["query"]),
            max_chains=limits.max_chains_per_goal,
        )
    ]
    assert len(answers) == 1, f"{case['id']} 应只有一个接地答案, 实际 {len(answers)}"
    return answers[0]


def _rule_ids(chains):
    return [c["rule_id"] for c in chains]


def _counter_kinds(answer, bucket):
    return [k for c in answer[bucket] for k in
            (cc["attack_kind"] for cc in c["counter_chains"])]


def _counter_rules(answer, bucket):
    return [cc["rule"] for c in answer[bucket] for cc in c["counter_chains"]]


@pytest.mark.parametrize(
    "case", _SINGLE_CASES, ids=[c["id"] for c in _SINGLE_CASES]
)
def test_expected_single_answers(settings, case):
    answer = _single_answer(case, settings.limits)
    expect = case["expect"]

    if "goal" in expect:
        assert answer["goal"] == expect["goal"]
    assert answer["status"] == expect["status"], case["id"]
    if "reason_code" in expect:
        assert answer["reason_code"] == expect["reason_code"], case["id"]

    counts = answer["chain_counts"]
    if "supporting" in expect:
        assert counts["supporting"] == expect["supporting"], case["id"]
    if "defeated" in expect:
        assert counts["defeated"] == expect["defeated"], case["id"]
    if "pending" in expect:
        assert counts["pending"] == expect["pending"], case["id"]

    if "supporting_rule_ids" in expect:
        assert _rule_ids(answer["supporting_chains"]) == expect["supporting_rule_ids"]
    if "defeated_rule_ids" in expect:
        assert _rule_ids(answer["defeated_chains"]) == expect["defeated_rule_ids"]
    if "pending_rule_ids" in expect:
        assert _rule_ids(answer["pending_chains"]) == expect["pending_rule_ids"]
    if "supporting_assumptions" in expect:
        all_assumptions = [
            a for c in answer["supporting_chains"] for a in c["assumptions"]
        ]
        assert all_assumptions == expect["supporting_assumptions"]
    if "counter_attack_kinds" in expect:
        bucket = (
            "defeated_chains" if counts["defeated"] else "pending_chains"
        )
        assert _counter_kinds(answer, bucket) == expect["counter_attack_kinds"]
    if "counter_rule_ids" in expect:
        bucket = (
            "defeated_chains" if counts["defeated"] else "pending_chains"
        )
        assert _counter_rules(answer, bucket) == expect["counter_rule_ids"]
    if "counter_detail_contains" in expect:
        bucket = (
            "defeated_chains" if counts["defeated"] else "pending_chains"
        )
        details = [cc["detail"] for c in answer[bucket] for cc in c["counter_chains"]]
        assert any(expect["counter_detail_contains"] in d for d in details)


@pytest.mark.parametrize(
    "case", _ENUM_CASES, ids=[c["id"] for c in _ENUM_CASES]
)
def test_expected_enumeration(settings, case):
    compiled = _compile(case, settings.limits)
    answers = [
        answer_to_dict(a)
        for a in answer_query(
            compiled,
            parse_query(case["query"]),
            max_chains=settings.limits.max_chains_per_goal,
        )
    ]
    actual = [
        {"goal": a["goal"], "status": a["status"], "substitution": a["substitution"]}
        for a in answers
    ]
    assert actual == case["expect_answers"]


@pytest.mark.parametrize(
    "case", _ERROR_CASES, ids=[c["id"] for c in _ERROR_CASES]
)
def test_expected_error_categories(settings, case):
    from app.errors import ReasonerError

    expected_category = case["category"]
    expected_error = case["error"]
    with pytest.raises(ReasonerError) as exc:
        compiled = _compile(case, settings.limits)  # noqa: F841
    assert exc.value.category.value == expected_category, case["id"]
    assert type(exc.value).__name__ == expected_error, case["id"]
    if "message_contains" in case:
        assert case["message_contains"] in exc.value.message
    if "detail_path" in case:
        container = exc.value.details
        for key in case["detail_path"]:
            assert key in container
            container = container[key]
