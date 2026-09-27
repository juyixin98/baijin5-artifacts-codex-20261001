"""资源耗尽: 超过配置上限必须抛 ResourceLimitError, 与其它错误类别可区分。

使用极小的临时上限构造 KernelLimits, 不需要真正制造海量输入。
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.errors import (
    KnowledgeStateError,
    ReasoningFailureError,
    ResourceLimitError,
    RuleLanguageError,
)
from app.kernel.facade import answer_query, compile_knowledge_base
from app.rulelang import parse_query, parse_theory


def _compile_with(limits, text):
    theory = parse_theory(text)
    return compile_knowledge_base(
        theory.facts, theory.all_rules, theory.priorities, limits
    )


def test_too_many_ground_instances_is_resource_exhausted(settings):
    tiny = replace(settings.limits, max_ground_instances=1)
    text = """
    A(1). A(2). A(3).
    @r B(X) :- A(X).
    """
    with pytest.raises(ResourceLimitError) as exc:
        _compile_with(tiny, text)
    assert exc.value.category.value == "resource_exhausted"
    assert exc.value.details["max_ground_instances"] == 1


def test_too_many_arguments_is_resource_exhausted(settings):
    # 一个事实经由大量可组合的独立前提可产生很多证明树; 这里把上限压到极低
    tiny = replace(settings.limits, max_arguments=1)
    text = """
    A(1).
    @r B(X) := A(X).
    @s C(X) := B(X).
    """
    with pytest.raises(ResourceLimitError) as exc:
        _compile_with(tiny, text)
    assert exc.value.category.value == "resource_exhausted"
    assert "max_arguments" in exc.value.details


def test_error_categories_are_distinct():
    # 四类错误的 category 值互不相同, 保证调用方可以区分
    values = {
        RuleLanguageError("x").category.value,
        KnowledgeStateError("x").category.value,
        ResourceLimitError("x").category.value,
        ReasoningFailureError("x").category.value,
    }
    assert values == {
        "input_error",
        "state_conflict",
        "resource_exhausted",
        "computation_failed",
    }
    # 计算失败独立于资源耗尽, 二者不可混为一谈
    assert (
        ReasoningFailureError("x").category.value
        != ResourceLimitError("x").category.value
    )


def test_too_many_chains_for_goal_is_resource_exhausted(settings):
    # 多条独立规则推出同一结论 => 多个论证; 把每目标链上限压到 0
    theory = parse_theory(
        """
        A(x). B(x).
        @r1 P(X) := A(X).
        @r2 P(X) := B(X).
        """
    )
    compiled = compile_knowledge_base(
        theory.facts, theory.all_rules, theory.priorities, settings.limits
    )
    with pytest.raises(ResourceLimitError) as exc:
        answer_query(compiled, parse_query("P(x)"), max_chains=0)
    assert exc.value.category.value == "resource_exhausted"
    assert "论证链数量" in exc.value.message


def test_fixpoint_iteration_limit_is_resource_error(monkeypatch, settings):
    # 论证数量很小, 不动点正常只需几步; 把迭代上限压到 0 以下不可配置为负,
    # 改为 1: grounded 至少需要"空集 -> 第一步"两轮比较, 单轮即触发上限保护。
    tiny = replace(settings.limits, fixpoint_iterations=0)
    text = "A. @r B := A."
    with pytest.raises(ResourceLimitError) as exc:
        _compile_with(tiny, text)
    assert exc.value.category.value == "resource_exhausted"
    assert "不动点" in exc.value.message
