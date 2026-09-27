"""解释层: 把接地标签组织成支持 / 击败 / 悬而未决三类论证链。

ChainView 是一棵可序列化的证明树, role 标明它相对查询目标的角色:
* supporting —— grounded 扩展中的支持链 (标签 accepted);
* defeated   —— 支持目标但被击败的链 (标签 rejected), counter_chains 给出击败者;
* pending    —— 支持目标但卷入未决冲突的链 (标签 undecided), counter_chains 给出对攻者。

当目标本身没有任何论证、但其强否定存在 grounded 链时, 目标状态为
rejected, 反方链放入 opposing_evidence (它不是"被击败的支持链")。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..errors import ReasoningFailureError, ResourceLimitError
from ..rulelang.ast_nodes import Literal
from .arguments import Argument
from .engine import AttackRecord, ReasonerEngine
from .reasons import REASON_TEXT, Status

ROLE_SUPPORTING = "supporting"
ROLE_DEFEATED = "defeated"
ROLE_PENDING = "pending"


@dataclass
class ChainView:
    chain_id: str
    role: str
    conclusion: str
    rule_id: Optional[str]
    rule_kind: Optional[str]
    assumptions: list[str]
    tree: dict
    counter_chains: list[dict] = field(default_factory=list)


@dataclass
class GoalAnswer:
    goal: str
    substitution: dict[str, object]
    status: str
    reason_code: str
    reason: str
    supporting: list[ChainView] = field(default_factory=list)
    defeated: list[ChainView] = field(default_factory=list)
    pending: list[ChainView] = field(default_factory=list)
    opposing_evidence: list[dict] = field(default_factory=list)

    def all_chains(self) -> list[ChainView]:
        return [*self.supporting, *self.defeated, *self.pending]


def render_argument_tree(arg: Argument) -> dict:
    return {
        "conclusion": arg.head.render(),
        "rule": arg.rule_id,
        "rule_kind": arg.kind.value if arg.kind else "fact",
        "assumptions": [lit.render() for lit in sorted(arg.assumptions, key=str)],
        "premises": [render_argument_tree(child) for child in arg.children],
    }


def _counter_view(engine: ReasonerEngine, record: AttackRecord) -> dict:
    attacker = engine.store.arguments[record.attacker_id]
    return {
        "conclusion": attacker.head.render(),
        "rule": attacker.rule_id,
        "rule_kind": attacker.kind.value if attacker.kind else "fact",
        "attack_kind": record.kind,
        "detail": record.detail,
        "tree": render_argument_tree(attacker),
    }


def build_goal_answer(
    engine: ReasonerEngine,
    goal: Literal,
    substitution: Optional[dict[str, object]] = None,
    *,
    max_chains: int,
) -> GoalAnswer:
    status, reason_code = engine.status_of_literal(goal)
    args_for = engine.store.for_head(goal)
    if len(args_for) > max_chains:
        raise ResourceLimitError(
            f"目标 {goal.render()} 的论证链数量 {len(args_for)} 超过上限 {max_chains}",
            details={
                "goal": goal.render(),
                "count": len(args_for),
                "max_chains": max_chains,
            },
        )

    answer = GoalAnswer(
        goal=goal.render(),
        substitution=substitution or {},
        status=status,
        reason_code=reason_code,
        reason=REASON_TEXT[reason_code],
    )

    role_to_bucket = {
        ROLE_SUPPORTING: answer.supporting,
        ROLE_DEFEATED: answer.defeated,
        ROLE_PENDING: answer.pending,
    }
    expected_label = {
        ROLE_SUPPORTING: Status.ACCEPTED,
        ROLE_DEFEATED: Status.REJECTED,
        ROLE_PENDING: Status.UNDECIDED,
    }

    def add_chain(arg: Argument, role: str) -> None:
        label = engine.label_of(arg)
        if label != expected_label[role]:
            raise ReasoningFailureError(
                f"链分类与标签不一致: {arg.head.render()} 标签 {label} 但角色 {role}",
                details={"conclusion": arg.head.render(), "label": label, "role": role},
            )
        counters: list[dict] = []
        if role == ROLE_DEFEATED:
            counters = [
                _counter_view(engine, r) for r in engine.defeating_attackers(arg)
            ]
        elif role == ROLE_PENDING:
            counters = [
                _counter_view(engine, r) for r in engine.pending_attackers(arg)
            ]
        bucket = role_to_bucket[role]
        bucket.append(
            ChainView(
                chain_id=f"{role}-{len(bucket) + 1}",
                role=role,
                conclusion=arg.head.render(),
                rule_id=arg.rule_id,
                rule_kind=arg.kind.value if arg.kind else "fact",
                assumptions=[a.render() for a in sorted(arg.assumptions, key=str)],
                tree=render_argument_tree(arg),
                counter_chains=counters,
            )
        )

    for arg in args_for:
        label = engine.label_of(arg)
        if label == Status.ACCEPTED:
            add_chain(arg, ROLE_SUPPORTING)
        elif label == Status.REJECTED:
            add_chain(arg, ROLE_DEFEATED)
        else:
            add_chain(arg, ROLE_PENDING)

    # 目标本身无支持链、相反结论有 grounded 链: 把反方证据显式列出
    if not args_for:
        opposite = goal.negate_strong()
        for arg in engine.store.for_head(opposite):
            if engine.label_of(arg) == Status.ACCEPTED:
                answer.opposing_evidence.append(
                    {
                        "conclusion": arg.head.render(),
                        "rule": arg.rule_id,
                        "rule_kind": arg.kind.value if arg.kind else "fact",
                        "attack_kind": "OPPOSITE_GROUNDED",
                        "detail": (
                            f"相反结论 {arg.head.render()} 存在 grounded 链, "
                            f"因此 {goal.render()} 被否决"
                        ),
                        "tree": render_argument_tree(arg),
                    }
                )

    return answer
