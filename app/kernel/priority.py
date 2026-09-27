"""规则优先关系。

* 严格规则按定义天然强于可撤销规则 (不依赖显式优先声明);
* 同类规则之间使用显式 priority(强, 弱) 边, 取传递闭包作为偏序;
* 构造时再次做无环校验 (存储层取出的数据同样不可信)。

比较结果:
    GREATER 本规则更强
    LESS    本规则更弱
    EQUAL   同一条规则
    NONE    不可比较 (验收规则 2: 不可比较时必须保留冲突)
"""
from __future__ import annotations

from enum import Enum

from ..errors import KnowledgeStateError
from ..rulelang.ast_nodes import RuleKind


class Compare(int, Enum):
    GREATER = 1
    EQUAL = 0
    LESS = -1
    NONE = 2


class PriorityRelation:
    """规则标识上的严格偏序 (含传递闭包)。"""

    def __init__(self, edges: list[tuple[str, str]]) -> None:
        self._edges: list[tuple[str, str]] = list(edges)
        self._greater: dict[str, set[str]] = {}
        for strong, weak in edges:
            self._greater.setdefault(strong, set()).add(weak)
        self._close_transitively()

    def _close_transitively(self) -> None:
        # Floyd-Warshall 风格的传递闭包, 同时检测环
        nodes = set(self._greater)
        for _, weak in self._edges:
            nodes.add(weak)
        changed = True
        while changed:
            changed = False
            for a in list(nodes):
                reach = self._greater.get(a)
                if not reach:
                    continue
                for mid in list(reach):
                    via = self._greater.get(mid)
                    if not via:
                        continue
                    added = via - reach
                    if added:
                        reach |= added
                        changed = True
        for node in nodes:
            if node in self._greater.get(node, set()):
                cycle = self._reconstruct_cycle(node)
                raise KnowledgeStateError(
                    f"优先关系存在环: {' > '.join(cycle)}",
                    details={"cycle": cycle},
                )

    def _reconstruct_cycle(self, node: str) -> list[str]:
        # 广度优先找回边 node -> ... -> node
        from collections import deque

        queue = deque([(node, [node])])
        seen = {node}
        while queue:
            current, path = queue.popleft()
            for nxt in self._greater.get(current, ()):  # type: ignore[union-attr]
                if nxt == node:
                    return path + [node]
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append((nxt, path + [nxt]))
        return [node, node]

    @property
    def edges(self) -> list[tuple[str, str]]:
        return list(self._edges)

    def compare_ids(self, strong_candidate: str, weak_candidate: str) -> Compare:
        if strong_candidate == weak_candidate:
            return Compare.EQUAL
        if weak_candidate in self._greater.get(strong_candidate, set()):
            return Compare.GREATER
        if strong_candidate in self._greater.get(weak_candidate, set()):
            return Compare.LESS
        return Compare.NONE

    def compare_kinds_and_ids(
        self, kind_a: RuleKind, id_a: str, kind_b: RuleKind, id_b: str
    ) -> Compare:
        """先按规则类别 (严格 > 可撤销), 再按显式偏序比较。"""
        if id_a == id_b:
            return Compare.EQUAL
        if kind_a is RuleKind.STRICT and kind_b is RuleKind.DEFEASIBLE:
            return Compare.GREATER
        if kind_a is RuleKind.DEFEASIBLE and kind_b is RuleKind.STRICT:
            return Compare.LESS
        return self.compare_ids(id_a, id_b)
