"""静态语义校验。

校验内容:
1. 事实必须接地 (不允许含变量)。
2. 同名谓词元数一致 (强否定与 NAF 不改变谓词元数)。
3. 规则安全性:
   - 规则头变量必须出现在规则体的正文字 (非 NAF) 中;
   - NAF 文字中的变量必须先被某个正文字约束;
   - 严格规则体不允许 NAF (严格规则是确定子句, 保证单调)。
4. 优先关系只能引用已定义规则, 且优先关系无环 (验收规则 1)。
"""
from __future__ import annotations

from .ast_nodes import Theory
from ..errors import RuleLanguageError


def validate_theory(theory: Theory) -> None:
    rules = theory.all_rules
    id_set = {r.rule_id for r in rules}

    _validate_facts_ground(theory)
    _validate_arities(theory)
    for rule in rules:
        _validate_rule_safety(rule)
    _validate_priorities(theory.priorities, id_set)


def _validate_facts_ground(theory: Theory) -> None:
    for fact in theory.facts:
        if fact.naf:
            raise RuleLanguageError(
                "事实不允许使用 not (失败即否定)",
                details={"literal": fact.render(), "line": fact.line},
            )
        if not fact.is_ground():
            raise RuleLanguageError(
                f"事实必须接地, 但 {fact.render()} 含自由变量 "
                f"{sorted(fact.variables())}",
                details={"literal": fact.render(), "line": fact.line},
            )


def _validate_arities(theory: Theory) -> None:
    arities: dict[str, int] = {}

    def check(lit, where: str) -> None:
        name = lit.predicate
        if name in arities and arities[name] != lit.arity:
            raise RuleLanguageError(
                f"谓词 {name}/{arities[name]} 与 {name}/{lit.arity} 元数冲突 ({where})",
                details={
                    "predicate": name,
                    "expected_arity": arities[name],
                    "actual_arity": lit.arity,
                    "where": where,
                    "literal": lit.render(),
                },
            )
        arities.setdefault(name, lit.arity)

    for fact in theory.facts:
        check(fact, "事实")
    for rule in theory.all_rules:
        check(rule.head, f"规则 @{rule.rule_id} 头")
        for lit in rule.body:
            check(lit, f"规则 @{rule.rule_id} 体")


def _validate_rule_safety(rule) -> None:
    positive_vars: set[str] = set()
    for lit in rule.body:
        if not lit.naf:
            positive_vars |= lit.variables()

    if rule.is_strict:
        for lit in rule.body:
            if lit.naf:
                raise RuleLanguageError(
                    f"严格规则 @{rule.rule_id} 的规则体不允许使用 not; "
                    "严格规则必须是单调确定子句",
                    details={"rule": rule.render(), "literal": lit.render()},
                )

    head_vars = rule.head.variables()
    unbound_head = head_vars - positive_vars
    if unbound_head:
        raise RuleLanguageError(
            f"规则 @{rule.rule_id} 不安全: 头变量 {sorted(unbound_head)} "
            "未被规则体正文字约束",
            details={"rule": rule.render(), "unbound": sorted(unbound_head)},
        )

    for lit in rule.body:
        if lit.naf:
            unbound = lit.variables() - positive_vars
            if unbound:
                raise RuleLanguageError(
                    f"规则 @{rule.rule_id} 不安全: not 文字 {lit.render()} 中变量 "
                    f"{sorted(unbound)} 未被正文字约束",
                    details={"rule": rule.render(), "unbound": sorted(unbound)},
                )


def _validate_priorities(priorities: list[tuple[str, str]], id_set: set[str]) -> None:
    # 去重保序
    seen: set[tuple[str, str]] = set()
    edges: list[tuple[str, str]] = []
    for strong, weak in priorities:
        if strong not in id_set:
            raise RuleLanguageError(
                f"优先关系引用了不存在的规则: @{strong}",
                details={"missing_rule": strong},
            )
        if weak not in id_set:
            raise RuleLanguageError(
                f"优先关系引用了不存在的规则: @{weak}",
                details={"missing_rule": weak},
            )
        if (strong, weak) not in seen:
            seen.add((strong, weak))
            edges.append((strong, weak))

    cycle = _find_cycle(edges)
    if cycle is not None:
        raise RuleLanguageError(
            f"优先关系存在环: {' > '.join(cycle)}",
            details={"cycle": cycle},
        )


def _find_cycle(edges: list[tuple[str, str]]) -> list[str] | None:
    """在优先图中寻找一个环, 返回环上的节点序列 (首尾同名)。"""
    adj: dict[str, list[str]] = {}
    for strong, weak in edges:
        adj.setdefault(strong, []).append(weak)

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = GRAY
        stack.append(node)
        for nxt in adj.get(node, []):
            if color.get(nxt, WHITE) == GRAY:
                start = stack.index(nxt)
                return stack[start:] + [nxt]
            if color.get(nxt, WHITE) == WHITE:
                found = visit(nxt)
                if found is not None:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for node in adj:
        if color.get(node, WHITE) == WHITE:
            found = visit(node)
            if found is not None:
                return found
    return None
