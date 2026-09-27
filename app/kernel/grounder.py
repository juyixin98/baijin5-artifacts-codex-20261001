"""接地器: 把带变量的规则实例化为有限的命题规则集合。

安全性由 validator 保证 (所有规则头/NAF 变量都被正文字约束),
因此自底向上按"可达原子集"迭代即可得到全部相关接地实例, 无需枚举
全域笛卡尔积。

NAF 文字不参与可达性推导 (它们不能产生新项), 只随正文字的置换
一并实例化。

资源上限: 实例数超过 max_ground_instances 抛 ResourceLimitError。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import ResourceLimitError
from ..rulelang.ast_nodes import Literal, Rule, RuleKind


@dataclass(frozen=True)
class GroundRule:
    """一条接地规则实例。

    body_sign 与 body 等长, True 表示该文字是正文字, False 表示 NAF。
    强否定已经编码在 Literal.negated 中。
    """

    rule_id: str
    kind: RuleKind
    head: Literal
    body: tuple[Literal, ...]
    body_sign: tuple[bool, ...]
    line: int = 0

    def positive_body(self) -> list[Literal]:
        return [lit for lit, sign in zip(self.body, self.body_sign) if sign]

    def naf_body(self) -> list[Literal]:
        return [lit for lit, sign in zip(self.body, self.body_sign) if not sign]


@dataclass
class GroundProgram:
    facts: set[Literal] = field(default_factory=set)
    instances: list[GroundRule] = field(default_factory=list)
    reachable: set[Literal] = field(default_factory=set)

    def all_rules_for(self, head: Literal) -> list[GroundRule]:
        return [r for r in self.instances if _same_atom(r.head, head)]

    def strict_rules_for(self, head: Literal) -> list[GroundRule]:
        return [
            r
            for r in self.instances
            if r.kind is RuleKind.STRICT and _same_atom(r.head, head)
        ]

    def all_rules(self) -> list[GroundRule]:
        return self.instances


Substitution = dict[str, object]


def _same_atom(a: Literal, b: Literal) -> bool:
    return (
        a.predicate == b.predicate
        and a.negated == b.negated
        and a.naf == b.naf
        and a.args == b.args
    )


class _Relations:
    """按 (谓词, 元数, 强否定符号) 索引的接地原子元组集合。"""

    def __init__(self) -> None:
        self._tables: dict[tuple[str, int, bool], set[tuple[object, ...]]] = {}

    def add(self, lit: Literal) -> bool:
        key = (lit.predicate, lit.arity, lit.negated)
        table = self._tables.setdefault(key, set())
        row = tuple(value for _, value in lit.args)
        if row in table:
            return False
        table.add(row)
        return True

    def rows(self, lit_like: Literal) -> set[tuple[object, ...]]:
        return self._tables.get(
            (lit_like.predicate, lit_like.arity, lit_like.negated), set()
        )

    def atoms(self) -> set[Literal]:
        result: set[Literal] = set()
        for (predicate, _arity, negated), rows in self._tables.items():
            for row in rows:
                result.add(
                    Literal(
                        predicate=predicate,
                        args=tuple(("c", v) for v in row),
                        negated=negated,
                    )
                )
        return result


def ground_program(
    facts: list[Literal],
    rules: list[Rule],
    *,
    max_instances: int,
) -> GroundProgram:
    """构造接地程序。facts 必须已接地。"""
    relations = _Relations()
    for fact in facts:
        relations.add(fact)

    instances: dict[tuple, GroundRule] = {}
    instance_count = 0

    changed = True
    # 最坏迭代次数: 与可达原子数同阶, 这里用实例/规则规模给一个宽松硬上限,
    # 超出由 fixpoint_iterations 在引擎层另行处理; 接地阶段用 2*可达集合上界。
    hard_rounds = max(100, max_instances)
    rounds = 0
    while changed:
        changed = False
        rounds += 1
        if rounds > hard_rounds:
            raise ResourceLimitError(
                "接地迭代轮数超过上限, 可能存在异常深的推导依赖",
                details={"rounds": rounds, "max_rounds": hard_rounds},
            )
        for rule in rules:
            positive_lits = [lit for lit in rule.body if not lit.naf]
            if not positive_lits:
                substitutions = [{}]
            else:
                substitutions = _join_body(positive_lits, relations)
            for subst in substitutions:
                instance = _instantiate(rule, subst)
                key = _instance_key(instance)
                if key in instances:
                    continue
                if instance_count + 1 > max_instances:
                    raise ResourceLimitError(
                        "接地规则实例数超过配置上限",
                        details={
                            "max_ground_instances": max_instances,
                            "rule_id": rule.rule_id,
                        },
                    )
                instances[key] = instance
                instance_count += 1
                changed = True
                if relations.add(instance.head):
                    # 新原子可能让后续规则产生新置换
                    changed = True

    program = GroundProgram(
        facts=set(facts),
        instances=list(instances.values()),
        reachable=relations.atoms(),
    )
    return program


def _instance_key(gr: GroundRule) -> tuple:
    return (
        gr.rule_id,
        gr.head.predicate,
        gr.head.negated,
        gr.head.args,
        tuple((lit.predicate, lit.negated, lit.naf, lit.args) for lit in gr.body),
    )


def _join_body(
    positive_lits: list[Literal], relations: _Relations
) -> list[Substitution]:
    """对规则体正文字做嵌套循环连接, 输出全部一致置换。"""
    substitutions: list[Substitution] = [{}]
    for lit in positive_lits:
        next_subs: list[Substitution] = []
        rows = relations.rows(lit)
        for subst in substitutions:
            for row in rows:
                unified = _unify_row(lit, row, subst)
                if unified is not None:
                    next_subs.append(unified)
        substitutions = next_subs
        if not substitutions:
            break
    return substitutions


def _unify_row(
    lit: Literal, row: tuple[object, ...], subst: Substitution
) -> Substitution | None:
    result = dict(subst)
    for term, value in zip(lit.args, row):
        kind, name = term
        if kind == "c":
            if name != value:
                return None
        else:
            existing = result.get(name)
            if existing is not None and existing != value:
                return None
            result[name] = value
    return result


def _instantiate(rule: Rule, subst: Substitution) -> GroundRule:
    head = _apply_subst(rule.head, subst)
    body: list[Literal] = []
    signs: list[bool] = []
    for lit in rule.body:
        body.append(_apply_subst(lit, subst))
        signs.append(not lit.naf)
    return GroundRule(
        rule_id=rule.rule_id,
        kind=rule.kind,
        head=head,
        body=tuple(body),
        body_sign=tuple(signs),
        line=rule.line,
    )


def _apply_subst(lit: Literal, subst: Substitution) -> Literal:
    args = []
    for kind, value in lit.args:
        if kind == "v":
            if value not in subst:
                raise ResourceLimitError(
                    f"接地失败: 变量 {value} 未被约束 (规则安全性校验可能缺失)",
                    details={"variable": str(value), "literal": lit.render()},
                )
            args.append(("c", subst[value]))
        else:
            args.append(("c", value))
    return Literal(
        predicate=lit.predicate,
        args=tuple(args),
        negated=lit.negated,
        naf=lit.naf,
    )
