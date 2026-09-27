"""Grounded 语义推理引擎。

攻击关系 (两种直接攻击 + 子论证传播):

1. 假设攻击 (undercut / NAF):
   论证 A 的结论等于论证 B 使用的 NAF 假设 ``not L`` 中的 L,
   则 A 攻击 B 中引入该假设的子论证, 并向上传播到 B 的全部超论证。

2. 反驳攻击 (rebuttal, 强否定):
   A、B 顶层结论互为强否定 (L 与 -L) 时按优先级定向:
   - 严格 (含事实) 强于可撤销;
   - 同为可撤销时按显式无环优先偏序;
   - 不可比较时互相攻击 => 冲突悬置 (验收规则 2)。

标签取 ABA 风格 grounded 扩展 (最小不动点, 自空集向上):
   D_0 = ∅;  D_{i+1} = { A | A 的每个攻击者都被 D_i 中某论证攻击 }
   G   = ⋃ D_i
在 G 中 => accepted; 被 G 攻击 => rejected; 其余 => undecided。
没有任何论证的文字是 no_evidence (未知), 绝不当成假 (验收规则 3)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import ReasoningFailureError, ResourceLimitError
from ..rulelang.ast_nodes import Literal, RuleKind
from .arguments import Argument, ArgumentStore, build_arguments
from .consistency import check_strict_consistency
from .grounder import GroundProgram
from .priority import Compare, PriorityRelation
from .reasons import ReasonCode, Status


@dataclass(frozen=True)
class AttackRecord:
    attacker_id: int
    target_id: int
    point_id: int       # 被直接命中的子论证
    kind: str           # ASSUMPTION | REBUTTAL
    detail: str


@dataclass
class GroundedLabeling:
    accepted: set[int] = field(default_factory=set)
    rejected: set[int] = field(default_factory=set)
    undecided: set[int] = field(default_factory=set)
    iterations: int = 0


def atom_key(lit: Literal) -> tuple:
    return (lit.predicate, lit.negated, lit.args)


class ReasonerEngine:
    """对一个固定的接地程序求值, 并回答查询。"""

    def __init__(
        self,
        program: GroundProgram,
        priority: PriorityRelation,
        *,
        max_arguments: int = 50000,
        fixpoint_iterations: int = 512,
    ) -> None:
        self.program = program
        self.priority = priority
        self.max_arguments = max_arguments
        self.fixpoint_iterations = fixpoint_iterations

        # 严格矛盾是状态冲突, 必须在任何非单调求值之前拦截
        check_strict_consistency(program)

        self.store: ArgumentStore = build_arguments(
            program, max_arguments=max_arguments
        )
        self.records: list[AttackRecord] = []
        self.attacks: dict[int, set[int]] = {}
        self.attacked_by: dict[int, set[int]] = {}
        self.labeling: GroundedLabeling = self._evaluate()

    # ------------------------------------------------------------------ #
    # 攻击构造
    # ------------------------------------------------------------------ #

    def _evaluate(self) -> GroundedLabeling:
        self._build_direct_attacks()
        self._propagate_to_super_arguments()
        self._index_attack_edges()
        return self._grounded_fixpoint()

    def _record_attack(
        self,
        attacker_id: int,
        target_id: int,
        point_id: int,
        kind: str,
        detail: str,
    ) -> None:
        self.records.append(
            AttackRecord(attacker_id, target_id, point_id, kind, detail)
        )

    def _build_direct_attacks(self) -> None:
        args = self.store.arguments

        # 1) 假设攻击: 证成 NAF 假设中的正文字
        for b in args:
            for assumed in b.own_assumptions:
                attackers = self.store.for_head(assumed)
                for a in attackers:
                    point = _subargument_introducing(b, assumed)
                    self._record_attack(
                        a.arg_id,
                        b.arg_id,
                        point.arg_id,
                        "ASSUMPTION",
                        f"证成 {assumed.render()}, 推翻该论证的假设 not {assumed.render()}",
                    )

        # 2) 反驳攻击: 仅在强否定对 L / -L 之间
        buckets: dict[tuple[str, tuple], dict[bool, list[Argument]]] = {}
        for a in args:
            base = (a.head.predicate, tuple(a.head.args))
            buckets.setdefault(base, {True: [], False: []})
            buckets[base][a.head.negated].append(a)
        for group in buckets.values():
            for positive in group[False]:
                for negative in group[True]:
                    self._add_rebuttal_pair(positive, negative)

    def _add_rebuttal_pair(self, a: Argument, b: Argument) -> None:
        """a 结论 L, b 结论 -L, 按顶层规则优先级定向。"""
        cmp_ab = self._compare_top(a, b)
        if cmp_ab is Compare.GREATER:
            self._record_attack(
                a.arg_id, b.arg_id, b.arg_id, "REBUTTAL",
                self._rebuttal_detail(a, b, cmp_ab),
            )
        elif cmp_ab is Compare.LESS:
            self._record_attack(
                b.arg_id, a.arg_id, a.arg_id, "REBUTTAL",
                self._rebuttal_detail(b, a, Compare.GREATER),
            )
        elif cmp_ab is Compare.NONE:
            # 不可比较 => 双向攻击 => grounded 语义下冲突悬置
            self._record_attack(
                a.arg_id, b.arg_id, b.arg_id, "REBUTTAL",
                self._rebuttal_detail(a, b, Compare.NONE),
            )
            self._record_attack(
                b.arg_id, a.arg_id, a.arg_id, "REBUTTAL",
                self._rebuttal_detail(b, a, Compare.NONE),
            )

    def _compare_top(self, a: Argument, b: Argument) -> Compare:
        kind_a = a.kind if a.kind is not None else RuleKind.STRICT
        kind_b = b.kind if b.kind is not None else RuleKind.STRICT
        id_a = a.rule_id if a.rule_id is not None else "<fact>"
        id_b = b.rule_id if b.rule_id is not None else "<fact>"
        return self.priority.compare_kinds_and_ids(kind_a, id_a, kind_b, id_b)

    @staticmethod
    def _rebuttal_detail(winner: Argument, loser: Argument, cmp: Compare) -> str:
        w = winner.rule_id or "<事实>"
        l = loser.rule_id or "<事实>"
        if cmp is Compare.NONE:
            return (
                f"{winner.head.render()} 与 {loser.head.render()} 结论相反; "
                f"规则 {w} 与 {l} 优先级不可比较, 冲突保留"
            )
        if (
            winner.rule_id is None
            or winner.kind is RuleKind.STRICT
        ) and loser.kind is RuleKind.DEFEASIBLE:
            basis = "严格规则/事实 强于可撤销规则"
        else:
            basis = f"显式优先关系 {w} > {l}"
        return f"{winner.head.render()} 反驳 {loser.head.render()} ({basis})"

    def _propagate_to_super_arguments(self) -> None:
        """若 X 攻击子论证 S, 则 X 攻击所有以 S 为子树的上层论证。"""
        # 父关系: arg_id -> 直接包含它的论证集合
        parents: dict[int, set[int]] = {}
        for arg in self.store.arguments:
            for child in arg.children:
                parents.setdefault(child.arg_id, set()).add(arg.arg_id)

        original = list(self.records)
        for record in original:
            # 从被命中点向上收集全部超论证
            seen: set[int] = set()
            frontier = [record.point_id]
            while frontier:
                current = frontier.pop()
                for parent in parents.get(current, ()):  # type: ignore[union-attr]
                    if parent not in seen:
                        seen.add(parent)
                        frontier.append(parent)
            for parent_id in seen:
                if parent_id == record.target_id:
                    continue
                self._record_attack(
                    record.attacker_id,
                    parent_id,
                    record.point_id,
                    record.kind,
                    f"经子论证传播: {record.detail}",
                )

    def _index_attack_edges(self) -> None:
        self.attacks = {a.arg_id: set() for a in self.store.arguments}
        self.attacked_by = {a.arg_id: set() for a in self.store.arguments}
        for record in self.records:
            if record.target_id in self.attacks[record.attacker_id]:
                continue
            self.attacks[record.attacker_id].add(record.target_id)
            self.attacked_by[record.target_id].add(record.attacker_id)

    # ------------------------------------------------------------------ #
    # Grounded 不动点
    # ------------------------------------------------------------------ #

    def _grounded_fixpoint(self) -> GroundedLabeling:
        args = self.store.arguments
        # 自空集向上取特征函数 F 的最小不动点:
        #   F(S) = { A | A 的每个攻击者都被 S 中某论证攻击 }
        current: set[int] = set()
        iterations = 0
        while True:
            iterations += 1
            if iterations > self.fixpoint_iterations:
                raise ResourceLimitError(
                    "grounded 不动点迭代超过上限",
                    details={
                        "fixpoint_iterations": self.fixpoint_iterations,
                        "argument_count": len(args),
                    },
                )
            nxt: set[int] = set()
            for arg in self.store.arguments:
                incoming = self.attacked_by[arg.arg_id]
                # A 成立的条件: 它的每个攻击者 X, 都被当前集合中的某个
                # 论证 D 攻击。"D 攻击 X" 要查 X 的入边 attacked_by[X],
                # 而不是 X 的出边 (X 攻击谁) —— 后者在非对称辩护链上
                # 会漏掉恢复 (reinstatement)。
                if all(
                    any(
                        defender in current
                        for defender in self.attacked_by.get(attacker, set())
                    )
                    for attacker in incoming
                ):
                    nxt.add(arg.arg_id)
            if nxt == current:
                break
            current = nxt

        accepted = current
        rejected: set[int] = set()
        for arg_id in accepted:
            rejected |= self.attacks.get(arg_id, set())
        undecided = {a.arg_id for a in args} - accepted - rejected

        if accepted & rejected:
            raise ReasoningFailureError(
                "grounded 标签内部错误: 论证同时被接受与否决",
                details={"intersection": sorted(accepted & rejected)},
            )
        return GroundedLabeling(accepted, rejected, undecided, iterations)

    # ------------------------------------------------------------------ #
    # 查询支持
    # ------------------------------------------------------------------ #

    def label_of(self, arg: Argument) -> str:
        if arg.arg_id in self.labeling.accepted:
            return Status.ACCEPTED
        if arg.arg_id in self.labeling.rejected:
            return Status.REJECTED
        return Status.UNDECIDED

    def status_of_literal(self, lit: Literal) -> tuple[str, str]:
        """返回接地文字的 (状态, 理由码)。"""
        args_for = self.store.for_head(lit)
        accepted = [a for a in args_for if self.label_of(a) == Status.ACCEPTED]
        if accepted:
            return Status.ACCEPTED, ReasonCode.ACCEPTED_GROUNDED

        opposite = lit.negate_strong()
        opposite_accepted = [
            a for a in self.store.for_head(opposite)
            if self.label_of(a) == Status.ACCEPTED
        ]
        if opposite_accepted:
            return Status.REJECTED, ReasonCode.REJECTED_OPPOSITE_GROUNDED

        if args_for:
            undecided = [a for a in args_for if self.label_of(a) == Status.UNDECIDED]
            if undecided:
                if any(self._is_in_mutual_conflict(a) for a in undecided):
                    return Status.UNDECIDED, ReasonCode.PENDING_MUTUAL_CONFLICT
                return Status.UNDECIDED, ReasonCode.PENDING_UNRESOLVED_ASSUMPTION
            return Status.REJECTED, ReasonCode.REJECTED_ALL_CHAINS_ATTACKED

        return Status.NO_EVIDENCE, ReasonCode.NO_EVIDENCE_AT_ALL

    def _is_in_mutual_conflict(self, arg: Argument) -> bool:
        """悬置论证是否卷入了相反结论之间的双向反驳。"""
        opposite = arg.head.negate_strong()
        for attacker_id in self.attacked_by.get(arg.arg_id, set()):
            attacker = self.store.arguments[attacker_id]
            if (
                attacker.head.predicate == opposite.predicate
                and attacker.head.args == opposite.args
                and attacker.head.negated == opposite.negated
                and attacker_id in self.labeling.undecided
            ):
                return True
        return False

    def defeating_attackers(self, arg: Argument) -> list[AttackRecord]:
        """导致 rejected 论证被否的具体攻击 (攻击者须在 grounded 集中)。"""
        result = []
        for record in self.records:
            if record.target_id != arg.arg_id:
                continue
            if record.attacker_id in self.labeling.accepted:
                result.append(record)
        return result

    def pending_attackers(self, arg: Argument) -> list[AttackRecord]:
        """悬置链面对的未决攻击。"""
        return [
            record
            for record in self.records
            if record.target_id == arg.arg_id
            and record.attacker_id in self.labeling.undecided
        ]


def _subargument_introducing(root: Argument, assumed: Literal) -> Argument:
    """找到在规则体中直接引入该 NAF 假设的最小 (最深) 子论证。"""
    introducers = [
        sub
        for sub in root.sub_arguments()
        if assumed in sub.own_assumptions
    ]
    if not introducers:
        # 退化情形: 假设只通过合并出现, 退回根节点
        return root
    introducers.sort(key=_tree_size)
    return introducers[0]


def _tree_size(arg: Argument) -> int:
    return 1 + sum(_tree_size(child) for child in arg.children)
