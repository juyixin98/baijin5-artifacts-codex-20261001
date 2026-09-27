"""判断理由码。

每个接地文字的最终标签都带一个稳定机器可读的 reason_code 与人类可读
reason, 便于测试断言与日志回放。
"""
from __future__ import annotations


class Status:
    ACCEPTED = "accepted"        # grounded 扩展中有论证支持
    REJECTED = "rejected"        # 被相反的 grounded 结论或 grounded 攻击者否决
    UNDECIDED = "undecided"      # 有论证但冲突悬而未决
    NO_EVIDENCE = "no_evidence"  # 完全没有论证 (未知, 不是假)


class ReasonCode:
    ACCEPTED_GROUNDED = "ACCEPTED_GROUNDED"
    REJECTED_OPPOSITE_GROUNDED = "REJECTED_OPPOSITE_GROUNDED"
    REJECTED_ALL_CHAINS_ATTACKED = "REJECTED_ALL_CHAINS_ATTACKED"
    PENDING_MUTUAL_CONFLICT = "PENDING_MUTUAL_CONFLICT"
    PENDING_UNRESOLVED_ASSUMPTION = "PENDING_UNRESOLVED_ASSUMPTION"
    NO_EVIDENCE_AT_ALL = "NO_EVIDENCE_AT_ALL"


REASON_TEXT = {
    ReasonCode.ACCEPTED_GROUNDED: "grounded 扩展中存在无争议论证链",
    ReasonCode.REJECTED_OPPOSITE_GROUNDED: "相反结论存在 grounded 论证链, 本结论被否决",
    ReasonCode.REJECTED_ALL_CHAINS_ATTACKED: "全部支持链均被 grounded 论证击败 (含 NAF 假设被证成)",
    ReasonCode.PENDING_MUTUAL_CONFLICT: "支持链与反对链优先级不可比较, 冲突保留 (不取先出现者)",
    ReasonCode.PENDING_UNRESOLVED_ASSUMPTION: "支持链依赖的 NAF 假设存在未决争议, 结论悬置",
    ReasonCode.NO_EVIDENCE_AT_ALL: "不存在任何支持或反对的论证链; 缺少证据不等于反面事实",
}
