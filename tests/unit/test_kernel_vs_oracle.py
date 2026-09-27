"""被测内核 vs 独立预言机的交叉核验。

预言机 (tests/fixtures/oracle.py) 不 import 任何 app.kernel 模块,
对每个枚举小理论独立实现接地/论证/grounded 不动点。
本测试断言: 对所有可达接地原子, 被测引擎的状态与预言机完全一致。
"""
from __future__ import annotations

import pytest

from app.kernel.facade import compile_knowledge_base
from app.rulelang import parse_theory
from tests.fixtures import oracle

# 一批手工设计、覆盖不同现象的小理论 (合成符号, 无真实数据)
SMALL_THEORIES = [
    # 0 纯严格链
    """
    A.
    @s1 B :- A.
    @s2 C :- B.
    """,
    # 1 鸟 + 企鹅优先
    """
    Bird(t). Penguin(t).
    @r1 Flies(X) := Bird(X).
    @r2 -Flies(X) := Penguin(X).
    priority(r2, r1).
    """,
    # 2 尼克松菱形 (悬置)
    """
    Q(n). R(n).
    @q P(X) := Q(X).
    @r -P(X) := R(X).
    """,
    # 3 NAF 默认成立
    """
    Bird(t).
    @w Wings(X) := Bird(X), not Wingless(X).
    """,
    # 4 NAF 假设被严格证成
    """
    Bird(t). Penguin(t).
    @wl Wingless(X) :- Penguin(X).
    @w Wings(X) := Bird(X), not Wingless(X).
    """,
    # 5 无种子的正依赖环 => 环上原子不可达/无证据
    """
    @lp P(X) := Q(X).
    @lq Q(X) := P(X).
    Bird(t).
    @u R(X) := Bird(X), not P(X).
    """,
    # 6 严格强于可撤销
    """
    B(a).
    @d C(X) := B(X).
    @s -C(X) :- B(X).
    """,
    # 7 三个可撤销默认, 两条优先边形成无环链
    """
    F(x). G(x). H(x).
    @a P(X) := F(X).
    @b -P(X) := G(X).
    @c P2(X) := H(X).
    priority(a, b).
    """,
    # 8 两个独立的冲突对, 一个有优先一个悬置
    """
    Q(n). R(n). K(m). J(m).
    @q P(X) := Q(X).
    @r -P(X) := R(X).
    @k S(X) := K(X).
    @j -S(X) := J(X).
    priority(k, j).
    """,
    # 9 严格链通过可撤销规则体传播, NAF 依赖悬置结论
    """
    A(x).
    @d1 B(X) := A(X).
    @d2 -B(X) := A(X).
    @u C(X) := B(X).
    """,
    # 10 非对称恢复链 (reinstatement): a 与 c 都推出 R, b 推出 -R;
    #   a>b (a 攻击 b), b>c (b 攻击 c)。a 无争议 => b 被否 =>
    #   c 的唯一攻击者 b 被 grounded 的 a 击败, c 必须恢复为 accepted。
    # 这是 grounded 特征函数"查攻击者入边"方向的关键用例 (出边写法会错误悬置 c)。
    """
    Af(x). Bf(x). Cf(x).
    @a R(X) := Af(X).
    @b -R(X) := Bf(X).
    @c R(X) := Cf(X).
    priority(a, b).
    priority(b, c).
    """,
    # 11 混合状态: 一个单向击败对 (b 无争议 accepted, c 被 rejected)
    # 与一个无关的互攻悬置对 (d/e) 共存于同一知识库。
    """
    Bfact(x). Cfact(x). Dfact(x). Efact(x).
    @b -R(X) := Bfact(X).
    @c R(X) := Cfact(X).
    @d S(X) := Dfact(X).
    @e -S(X) := Efact(X).
    priority(b, c).
    """,
    # 12 NAF 假设构成的有向三环 a→c→b→a: 每个论证都被另一个未被击败的
    # 论证攻击, 三者必须全部悬置, 既不被接受也不被否决, 且求值不得死循环。
    """
    F(x).
    @a P(X) := F(X), not Q(X).
    @b Q(X) := F(X), not R(X).
    @c R(X) := F(X), not P(X).
    """,
]


def _atom(lit):
    return (
        lit.predicate,
        tuple(value for _, value in lit.args),
        lit.negated,
    )


@pytest.mark.parametrize("idx", range(len(SMALL_THEORIES)), ids=[f"theory-{i}" for i in range(len(SMALL_THEORIES))])
def test_kernel_matches_independent_oracle(settings, idx):
    text = SMALL_THEORIES[idx]
    theory = parse_theory(text)

    # 被测引擎
    compiled = compile_knowledge_base(
        theory.facts, theory.all_rules, theory.priorities, settings.limits
    )
    engine = compiled.engine
    engine_states = {}
    for lit in engine.program.reachable:
        status, _reason = engine.status_of_literal(lit)
        engine_states[_atom(lit)] = status

    # 独立预言机
    oracle_states, oracle_facts = oracle.evaluate(theory, theory.priorities)

    # 可达原子集合必须一致 (接地器行为交叉验证)
    assert set(engine_states) == oracle_facts, (
        f"theory-{idx} 可达原子集合不一致\n"
        f"engine={sorted(map(str, set(engine_states) - oracle_facts))}\n"
        f"oracle={sorted(map(str, oracle_facts - set(engine_states)))}"
    )

    mismatches = []
    for atom, oracle_status in oracle_states.items():
        engine_status = engine_states.get(atom)
        if engine_status != oracle_status:
            mismatches.append((atom, engine_status, oracle_status))
    assert not mismatches, (
        f"theory-{idx} 状态不一致 (engine vs oracle): {mismatches}"
    )


def test_no_evidence_atoms_agree(settings):
    # 查询完全不可达的原子, 两边都应表达"无证据"
    text = SMALL_THEORIES[0]
    theory = parse_theory(text)
    compiled = compile_knowledge_base(
        theory.facts, theory.all_rules, theory.priorities, settings.limits
    )
    from app.rulelang import parse_literal

    ghost = parse_literal("Unrelated(ghost)")
    status, reason = compiled.engine.status_of_literal(ghost)
    assert status == "no_evidence"
    assert reason == "NO_EVIDENCE_AT_ALL"
    # 预言机的结果字典中也不存在该原子
    oracle_states, _ = oracle.evaluate(theory, theory.priorities)
    assert _atom(ghost) not in oracle_states
