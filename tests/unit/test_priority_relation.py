"""优先关系: 类别天然序 + 显式无环偏序 + 存储数据再校验。"""
from __future__ import annotations

import pytest

from app.errors import KnowledgeStateError
from app.kernel.priority import Compare, PriorityRelation
from app.rulelang.ast_nodes import RuleKind


def test_explicit_priority_direction():
    rel = PriorityRelation([("a", "b")])
    assert rel.compare_ids("a", "b") is Compare.GREATER
    assert rel.compare_ids("b", "a") is Compare.LESS
    assert rel.compare_ids("a", "c") is Compare.NONE


def test_transitive_priority():
    rel = PriorityRelation([("a", "b"), ("b", "c")])
    assert rel.compare_ids("a", "c") is Compare.GREATER
    assert rel.compare_ids("c", "a") is Compare.LESS
    assert rel.compare_ids("a", "b") is Compare.GREATER


def test_strict_kind_beats_defeasible_without_explicit_edge():
    rel = PriorityRelation([])
    assert rel.compare_kinds_and_ids(
        RuleKind.STRICT, "s", RuleKind.DEFEASIBLE, "d"
    ) is Compare.GREATER
    assert rel.compare_kinds_and_ids(
        RuleKind.DEFEASIBLE, "d", RuleKind.STRICT, "s"
    ) is Compare.LESS


def test_two_defeasible_without_edge_are_incomparable():
    rel = PriorityRelation([])
    assert rel.compare_kinds_and_ids(
        RuleKind.DEFEASIBLE, "a", RuleKind.DEFEASIBLE, "b"
    ) is Compare.NONE


@pytest.mark.parametrize(
    "edges",
    [
        [("a", "b"), ("b", "a")],
        [("a", "b"), ("b", "c"), ("c", "a")],
    ],
)
def test_cycle_loaded_from_storage_is_still_rejected(edges):
    # 即使绕过解析器直接构造 (模拟从库中读出脏数据), 也必须拦截
    with pytest.raises(KnowledgeStateError) as exc:
        PriorityRelation(edges)
    assert exc.value.category.value == "state_conflict"
    assert "环" in exc.value.message
