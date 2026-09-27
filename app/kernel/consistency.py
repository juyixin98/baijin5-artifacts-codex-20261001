"""严格理论一致性检查。

仅用事实 + 严格规则做前向不动点 (严格规则不含 NAF, 单调)。
若同一个接地原子的正/反两个形式同时被严格推出, 则理论存在严格矛盾,
拒绝加载 (状态冲突), 因为严格矛盾会污染一切推导。
"""
from __future__ import annotations

from ..rulelang.ast_nodes import Literal, RuleKind
from .grounder import GroundProgram


def strict_closure(program: GroundProgram) -> set[Literal]:
    """返回严格规则 + 事实的最小闭包。"""
    derived: set[Literal] = set(program.facts)
    strict = [
        inst
        for inst in program.instances
        if inst.kind is RuleKind.STRICT
    ]
    changed = True
    while changed:
        changed = False
        for inst in strict:
            body = inst.positive_body()  # 严格规则体无 NAF
            if all(lit in derived for lit in body) and inst.head not in derived:
                derived.add(inst.head)
                changed = True
    return derived


def find_strict_contradiction(
    closure: set[Literal],
) -> tuple[Literal, Literal] | None:
    seen: dict[tuple[str, tuple], Literal] = {}
    for lit in closure:
        atom_key = (lit.predicate, tuple(a for a in lit.args))
        opposite = seen.get(atom_key)
        if opposite is not None and opposite.negated != lit.negated:
            return (opposite, lit)
        seen.setdefault(atom_key, lit)
    return None


def check_strict_consistency(program: GroundProgram) -> None:
    """抛 KnowledgeStateError 当且仅当严格闭包内含矛盾对。"""
    from ..errors import KnowledgeStateError

    closure = strict_closure(program)
    pair = find_strict_contradiction(closure)
    if pair is not None:
        pos, neg = (pair[1], pair[0]) if pair[0].negated else pair
        raise KnowledgeStateError(
            f"严格理论不一致: {pos.render()} 与 {neg.render()} 可同时由严格规则/事实推出",
            details={
                "positive": pos.render(),
                "negative": neg.render(),
                "closure_size": len(closure),
            },
        )
