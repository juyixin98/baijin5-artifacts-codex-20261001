"""规则重排不变性: 同一条理论的语句任意重排, 结论必须完全一致。

做法: 取若干小理论, 将顶层语句做多种排列 (事实/规则/优先关系),
对每种排列编译并计算"全部可达原子 -> 状态"映射, 断言映射恒定。
"""
from __future__ import annotations

import itertools

import pytest

from app.kernel.facade import compile_knowledge_base
from app.rulelang import parse_theory

THEORIES = [
    """
    Bird(t). Penguin(t).
    @r1 Flies(X) := Bird(X).
    @r2 -Flies(X) := Penguin(X).
    priority(r2, r1).
    """,
    """
    Q(n). R(n).
    @q P(X) := Q(X).
    @r -P(X) := R(X).
    """,
    """
    A(x). B(x).
    @a C(X) := A(X).
    @b -C(X) := B(X).
    @s D(X) :- C(X).
    priority(a, b).
    """,
    """
    Bird(t).
    @w Wings(X) := Bird(X), not Wingless(X).
    @wl Wingless(X) :- Penguin(X).
    Penguin(t).
    """,
]


def _statements(text: str) -> list[str]:
    raw = [s.strip() for s in text.split(".")]
    return [s for s in raw if s and not s.startswith("%")]


def _status_map(text, limits):
    theory = parse_theory(text)
    compiled = compile_knowledge_base(
        theory.facts, theory.all_rules, theory.priorities, limits
    )
    result = {}
    for lit in compiled.engine.program.reachable:
        status, reason = compiled.engine.status_of_literal(lit)
        result[lit.render()] = (status, reason)
    return result, compiled.stats()


@pytest.mark.parametrize("idx", range(len(THEORIES)), ids=[f"theory-{i}" for i in range(len(THEORIES))])
def test_status_invariant_under_permutations(settings, idx):
    base = _statements(THEORIES[idx])
    reference, ref_stats = _status_map(THEORIES[idx], settings.limits)

    seen_permutations = 0
    for perm in itertools.permutations(base):
        reordered = ".\n".join(perm) + "."
        statuses, stats = _status_map(reordered, settings.limits)
        assert statuses == reference, (
            f"theory-{idx} 重排后结论变化:\n"
            f"original={reference}\nreordered={statuses}"
        )
        # 接地实例数与论证数也不应依赖书写顺序
        assert stats["ground_instances"] == ref_stats["ground_instances"]
        assert stats["arguments"] == ref_stats["arguments"]
        seen_permutations += 1

    # 尼克松/企鹅等理论至少有 6 个语句, 全排列 720 种, 确认确实枚举了多种顺序
    assert seen_permutations >= 6
