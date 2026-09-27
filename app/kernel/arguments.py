"""论证 (证明树) 构造器。

一个论证是以某个接地结论为根的有限推导树:
* 叶节点是事实 (无规则的公理论证);
* 内部节点是一条接地严格/可撤销规则的应用, 子论证对应规则体正文字;
* 规则体中的 NAF 文字 ``not L`` 不作为子论证, 而是成为该论证使用的
  *假设* (assumption): 论证默认成立, 直到有人证明 L。

构造保证:
1. 自底向上迭代 (最小不动点), 正依赖循环 (p :- q, q :- p 且无事实
   支撑) 不产生任何论证 —— "缺少证据不等于反面事实", 这些文字保持
   悬而未决, 而不是被当作假。
2. 同一接地规则实例不得在一棵证明树中重复出现 (无环论证)。
   这不改变可证性: 任何含环证明都可通过删环得到无环证明。
3. 按结构签名去重, 论证集合有限。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Optional

from ..errors import ResourceLimitError
from ..rulelang.ast_nodes import Literal, RuleKind
from .grounder import GroundProgram, GroundRule


@dataclass(frozen=True)
class Argument:
    """一棵规范化的有限无环证明树。"""

    arg_id: int
    head: Literal
    rule_id: Optional[str]
    kind: Optional[RuleKind]
    instance_key: tuple
    children: tuple["Argument", ...]
    own_assumptions: frozenset[Literal]   # 本节点规则体里的 NAF 文字
    assumptions: frozenset[Literal]       # 整棵树的全部 NAF 假设
    rule_nodes: frozenset[tuple]          # 树中出现的全部规则实例键

    @property
    def is_axiom(self) -> bool:
        return self.rule_id is None

    @property
    def is_strict(self) -> bool:
        """严格论证: 公理论证或整棵树只使用严格规则。"""
        if self.is_axiom:
            return True
        if self.kind is not RuleKind.STRICT:
            return False
        return all(child.is_strict for child in self.children)

    def sub_arguments(self) -> list["Argument"]:
        result = [self]
        for child in self.children:
            result.extend(child.sub_arguments())
        return result


@dataclass
class ArgumentStore:
    arguments: list[Argument] = field(default_factory=list)
    by_head: dict[tuple, list[Argument]] = field(default_factory=dict)
    _sig_to_id: dict[tuple, int] = field(default_factory=dict)
    _head_literals: dict[tuple, Literal] = field(default_factory=dict)

    def for_head(self, lit: Literal) -> list[Argument]:
        return self.by_head.get(_atom_key(lit), [])

    def head_literal(self, key: tuple) -> Optional[Literal]:
        return self._head_literals.get(key)

    def all_heads(self) -> list[Literal]:
        return list(self._head_literals.values())


def _atom_key(lit: Literal) -> tuple:
    return (lit.predicate, lit.negated, lit.args)


def rule_app_key(inst: GroundRule) -> tuple:
    return (
        inst.rule_id,
        inst.kind.value,
        _atom_key(inst.head),
        tuple(
            (lit.predicate, lit.negated, lit.naf, lit.args)
            for lit, sign in zip(inst.body, inst.body_sign)
            if sign
        ),
    )


def build_arguments(
    program: GroundProgram, *, max_arguments: int
) -> ArgumentStore:
    store = ArgumentStore()

    def intern(
        head: Literal,
        rule_id: Optional[str],
        kind: Optional[RuleKind],
        instance_key: tuple,
        children: tuple[Argument, ...],
        own_assumptions: frozenset[Literal],
        assumptions: frozenset[Literal],
        rule_nodes: frozenset[tuple],
    ) -> tuple[Argument, bool]:
        sig = (instance_key, tuple(child.arg_id for child in children))
        existing = store._sig_to_id.get(sig)
        if existing is not None:
            return store.arguments[existing], False
        if len(store.arguments) >= max_arguments:
            raise ResourceLimitError(
                "论证 (证明树) 数量超过配置上限",
                details={
                    "max_arguments": max_arguments,
                    "near_conclusion": head.render(),
                },
            )
        arg = Argument(
            arg_id=len(store.arguments),
            head=head,
            rule_id=rule_id,
            kind=kind,
            instance_key=instance_key,
            children=children,
            own_assumptions=own_assumptions,
            assumptions=assumptions,
            rule_nodes=rule_nodes,
        )
        store.arguments.append(arg)
        store._sig_to_id[sig] = arg.arg_id
        key = _atom_key(head)
        store.by_head.setdefault(key, []).append(arg)
        store._head_literals.setdefault(key, head)
        return arg, True

    # 1) 事实 => 公理论证
    for fact in sorted(program.facts, key=lambda lit: lit.render()):
        fact_key = ("fact", _atom_key(fact))
        intern(
            fact,
            None,
            None,
            fact_key,
            (),
            frozenset(),
            frozenset(),
            frozenset({fact_key}),
        )

    # 2) 按规则实例自底向上闭包
    instances = sorted(
        program.instances,
        key=lambda inst: (inst.kind.value, inst.rule_id, rule_app_key(inst)),
    )
    changed = True
    while changed:
        changed = False
        for inst in instances:
            positive = [
                lit for lit, sign in zip(inst.body, inst.body_sign) if sign
            ]
            naf_lits = frozenset(
                lit for lit, sign in zip(inst.body, inst.body_sign) if not sign
            )
            child_options = [store.for_head(lit) for lit in positive]
            if any(options == [] for options in child_options):
                continue
            app_key = rule_app_key(inst)
            for combo in itertools.product(*child_options):
                # 无环保证: 同一接地规则实例不得重复出现在树中
                if any(app_key in child.rule_nodes for child in combo):
                    continue
                assumptions = naf_lits.union(
                    *itertools.chain(
                        (child.assumptions for child in combo),
                        [frozenset()],
                    )
                )
                rule_nodes = frozenset({app_key}).union(
                    *itertools.chain(
                        (child.rule_nodes for child in combo),
                        [frozenset()],
                    )
                )
                _, created = intern(
                    inst.head,
                    inst.rule_id,
                    inst.kind,
                    app_key,
                    tuple(combo),
                    naf_lits,
                    assumptions,
                    rule_nodes,
                )
                if created:
                    changed = True
    return store
