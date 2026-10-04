"""协议核心:服务器侧轮次状态机、活跃集合冻结、受限掉线恢复.

安全不变量(本模块强制,测试直接攻击这些不变量):

1. 活跃集合在 MASKED -> RECOVERY 推进时冻结,之后任何集合变更
   (新增客户端、补交掩码输入、篡改恢复目标)都被拒绝。
2. 对同一目标客户端,恢复阶段只允许收集一类份额:
   - 存活客户端 -> 掩码种子 b 的份额(用于去掉自掩码)
   - 掉线客户端 -> 协商私钥 c_SK 的份额(用于重算成对掩码)
   同一目标同时出现两类份额即判定集合变更/越权攻击,拒绝并审计。
3. 存活数低于门限 t 时明确中止(ABORTED),不输出任何结果。

假设(半诚实 + 不串谋):服务器好奇但按协议可观测地行动,
客户端不串谋;不防御恶意服务器与客户端合谋,不宣称生产安全。
"""

from __future__ import annotations

import base64
import enum

from . import crypto_adapters as crypto
from . import shamir
from .encoding import EncodingParams, add_vectors_mod, sub_vectors_mod
from .errors import (
    computation_failure,
    input_error,
    resource_exhausted,
    state_conflict,
)
from .state import StateStore


class Phase(str, enum.Enum):
    KEYS = "KEYS"            # 轮0: 广播密钥
    SHARES = "SHARES"        # 轮1: 分发加密份额
    MASKED = "MASKED"        # 轮2: 提交掩码输入
    RECOVERY = "RECOVERY"    # 轮3: 掉线恢复(活跃集合已冻结)
    DONE = "DONE"
    ABORTED = "ABORTED"


PHASE_ORDER = [Phase.KEYS, Phase.SHARES, Phase.MASKED, Phase.RECOVERY, Phase.DONE]

MAX_CLIENTS_PER_RUN = shamir.MAX_SHARES


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    try:
        return base64.b64decode(s.encode("ascii"), validate=True)
    except Exception as exc:
        raise input_error("bad_base64", "base64 解码失败", value=s[:32]) from exc


class SecAggServer:
    """一次运行 = 一个 run_id;配置在建 run 时固定。"""

    def __init__(self, store: StateStore) -> None:
        self.store = store

    # ---- 内部工具 ----

    def _run(self, run_id: str):
        row = self.store.get_run(run_id)
        if row is None:
            raise input_error("unknown_run", "运行不存在", run_id=run_id)
        return row

    def _config(self, run_id: str) -> dict:
        import json

        return json.loads(self._run(run_id)["config"])

    def _params(self, run_id: str) -> EncodingParams:
        cfg = self._config(run_id)
        return EncodingParams(
            scale=cfg["scale"],
            elem_bound=cfg["elem_bound"],
            max_clients=len(cfg["client_ids"]),
            max_vector_len=cfg["max_vector_len"],
        )

    def _require_phase(self, run_id: str, *phases: Phase) -> Phase:
        row = self._run(run_id)
        current = Phase(row["phase"]) if row["status"] == "ACTIVE" else Phase(row["status"])
        if current not in phases:
            raise state_conflict(
                "wrong_phase",
                f"当前阶段 {current.value} 不允许该操作",
                run_id=run_id,
                expected=[p.value for p in phases],
            )
        return current

    def _audit(self, run_id: str, event: str, rationale: str) -> None:
        phase = self._run(run_id)["phase"]
        self.store.audit(run_id, phase, event, rationale)

    # ---- 建 run ----

    def create_run(
        self,
        client_ids: list[str],
        threshold: int,
        vector_len: int,
        scale: int = 1 << 20,
        elem_bound: int = 1 << 40,
        max_vector_len: int = 1 << 16,
    ) -> str:
        if len(client_ids) != len(set(client_ids)):
            raise input_error("dup_client_id", "客户端 ID 重复")
        n = len(client_ids)
        if n < 3:
            raise input_error("too_few_clients", "安全聚合至少需要 3 个客户端", n=n)
        if n > MAX_CLIENTS_PER_RUN:
            raise resource_exhausted(
                "too_many_clients", "客户端数超过份额上限",
                n=n, max_clients=MAX_CLIENTS_PER_RUN,
            )
        if not 2 <= threshold <= n:
            raise input_error(
                "bad_threshold", "门限必须满足 2 <= t <= n", threshold=threshold, n=n
            )
        if vector_len < 1 or vector_len > max_vector_len:
            raise resource_exhausted(
                "bad_vector_len", "向量长度超出允许范围",
                vector_len=vector_len, max_vector_len=max_vector_len,
            )
        params = EncodingParams(
            scale=scale, elem_bound=elem_bound, max_clients=n,
            max_vector_len=max_vector_len,
        )
        params.validate()  # 求和防溢出上界
        config = {
            "client_ids": sorted(client_ids),
            "threshold": threshold,
            "vector_len": vector_len,
            "scale": scale,
            "elem_bound": elem_bound,
            "max_vector_len": max_vector_len,
        }
        return self.store.create_run(config)

    # ---- 轮0: 密钥 ----

    def submit_keys(self, run_id: str, client_id: str, c_pk: bytes, s_pk: bytes) -> dict:
        self._require_phase(run_id, Phase.KEYS)
        cfg = self._config(run_id)
        if client_id not in cfg["client_ids"]:
            self._audit(run_id, "reject_keys", f"未声明的客户端 {client_id} 尝试注册")
            raise input_error("unknown_client", "客户端不在本运行声明集合内",
                              client_id=client_id)
        if len(c_pk) != crypto.KEY_BYTES or len(s_pk) != crypto.KEY_BYTES:
            raise input_error("bad_key_len", "公钥必须为 32 字节",
                              c_pk_len=len(c_pk), s_pk_len=len(s_pk))
        existing = {r["client_id"] for r in self.store.get_clients(run_id)}
        if client_id in existing:
            raise state_conflict("dup_keys", "客户端已提交过密钥", client_id=client_id)
        self.store.add_client_keys(run_id, client_id, c_pk, s_pk)
        self._audit(run_id, "keys_accepted", f"客户端 {client_id} 密钥已登记")
        return {"registered": len(existing) + 1, "expected": len(cfg["client_ids"])}

    def get_public_keys(self, run_id: str) -> dict[str, dict[str, str]]:
        self._require_phase(run_id, Phase.KEYS, Phase.SHARES, Phase.MASKED,
                            Phase.RECOVERY, Phase.DONE)
        return {
            r["client_id"]: {"c_pk": _b64e(r["c_pk"]), "s_pk": _b64e(r["s_pk"])}
            for r in self.store.get_clients(run_id)
        }

    # ---- 轮1: 加密份额 ----

    def submit_shares(self, run_id: str, sender: str,
                      ciphertexts: dict[str, str]) -> dict:
        self._require_phase(run_id, Phase.SHARES)
        registered = {r["client_id"] for r in self.store.get_clients(run_id)}
        if sender not in registered:
            raise input_error("unknown_client", "发送方未注册密钥", client_id=sender)
        expected = registered - {sender}
        if set(ciphertexts) != expected:
            raise input_error(
                "share_recipients_mismatch",
                "份额接收方集合必须等于除自己外的全部已注册客户端",
                missing=sorted(expected - set(ciphertexts)),
                extra=sorted(set(ciphertexts) - expected),
            )
        if self.store.has_shares_from(run_id, sender):
            raise state_conflict("dup_shares", "该客户端已提交过份额", client_id=sender)
        for recipient, ct_b64 in ciphertexts.items():
            self.store.put_share(run_id, sender, recipient, _b64d(ct_b64))
        self._audit(run_id, "shares_accepted",
                    f"客户端 {sender} 的 {len(ciphertexts)} 份加密份额已登记")
        return {"stored": len(ciphertexts)}

    def get_shares_for(self, run_id: str, recipient: str) -> dict[str, str]:
        self._require_phase(run_id, Phase.MASKED, Phase.RECOVERY, Phase.DONE)
        rows = self.store.get_shares_for(run_id, recipient)
        if not rows:
            raise input_error("no_shares", "没有发给该客户端的份额",
                              client_id=recipient)
        return {r["sender"]: _b64e(r["ciphertext"]) for r in rows}

    def share_senders(self, run_id: str) -> list[str]:
        return self.store.get_share_senders(run_id)

    # ---- 轮2: 掩码输入 ----

    def submit_masked_input(self, run_id: str, client_id: str,
                            vector: list[int]) -> dict:
        self._require_phase(run_id, Phase.MASKED)
        cfg = self._config(run_id)
        senders = set(self.share_senders(run_id))
        if client_id not in senders:
            # 集合变更攻击面之一:未参与轮1的客户端试图混入
            self._audit(run_id, "reject_masked",
                        f"客户端 {client_id} 不在轮1发送方集合内,拒绝掩码输入")
            raise input_error("unknown_client", "客户端未参与轮1,不能提交掩码输入",
                              client_id=client_id)
        if len(vector) != cfg["vector_len"]:
            raise input_error("bad_vector_len", "向量长度与运行配置不符",
                              expected=cfg["vector_len"], got=len(vector))
        from .encoding import MODULUS

        if not all(isinstance(v, int) and 0 <= v < MODULUS for v in vector):
            raise input_error("elem_not_in_ring", "向量元素必须为 [0, R) 内整数")
        existing = self.store.get_masked_inputs(run_id)
        if client_id in existing:
            raise state_conflict("dup_masked", "该客户端已提交过掩码输入",
                                 client_id=client_id)
        self.store.put_masked_input(run_id, client_id, vector)
        self._audit(run_id, "masked_accepted",
                    f"客户端 {client_id} 掩码输入已登记 ({len(existing)+1}/{len(senders)})")
        return {"received": len(existing) + 1}

    # ---- 阶段推进(冻结点) ----

    def advance(self, run_id: str) -> dict:
        row = self._run(run_id)
        if row["status"] != "ACTIVE":
            raise state_conflict("run_closed", "运行已结束,不能推进",
                                 status=row["status"])
        phase = Phase(row["phase"])
        cfg = self._config(run_id)
        t = cfg["threshold"]

        if phase is Phase.KEYS:
            n_keys = len(self.store.get_clients(run_id))
            if n_keys < t:
                raise state_conflict(
                    "below_threshold", "注册密钥数低于门限,不能进入轮1",
                    registered=n_keys, threshold=t)
            self.store.set_phase(run_id, Phase.SHARES.value)
            self._audit(run_id, "phase_advanced",
                        f"KEYS->SHARES: {n_keys} 个客户端已注册")

        elif phase is Phase.SHARES:
            senders = self.share_senders(run_id)
            if len(senders) < t:
                raise state_conflict(
                    "below_threshold", "提交份额的客户端数低于门限",
                    senders=len(senders), threshold=t)
            self.store.set_phase(run_id, Phase.MASKED.value)
            self._audit(run_id, "phase_advanced",
                        f"SHARES->MASKED: 轮1发送方集合 {senders}")

        elif phase is Phase.MASKED:
            # 冻结点:活跃集合 = 已提交掩码输入的客户端,之后不可变更
            active = sorted(self.store.get_masked_inputs(run_id).keys())
            if len(active) < t:
                reason = (f"存活客户端 {len(active)} 低于门限 {t},"
                          f"协议明确中止")
                self.store.set_aborted(run_id, reason)
                self._audit(run_id, "run_aborted", reason)
                raise computation_failure("below_threshold", reason,
                                          active=len(active), threshold=t)
            self.store.freeze_active_set(run_id, active)
            self.store.set_phase(run_id, Phase.RECOVERY.value)
            self._audit(run_id, "active_set_frozen",
                        f"MASKED->RECOVERY: 活跃集合冻结为 {active}")

        elif phase is Phase.RECOVERY:
            self._maybe_finalize(run_id, force=True)

        return {"phase": self._run(run_id)["phase"], "status": self._run(run_id)["status"]}

    def get_active_set(self, run_id: str) -> dict:
        self._require_phase(run_id, Phase.RECOVERY, Phase.DONE)
        active = self.store.get_active_set(run_id)
        dropped = sorted(set(self.share_senders(run_id)) - set(active))
        return {"active": active, "dropped": dropped,
                "threshold": self._config(run_id)["threshold"]}

    # ---- 轮3: 恢复 ----

    def submit_recovery(self, run_id: str, sender: str,
                        b_shares: dict[str, list],
                        sk_shares: dict[str, list]) -> dict:
        self._require_phase(run_id, Phase.RECOVERY)
        sets = self.get_active_set(run_id)
        active, dropped = set(sets["active"]), set(sets["dropped"])

        if sender not in active:
            self._audit(run_id, "reject_recovery",
                        f"发送方 {sender} 不在冻结活跃集合内")
            raise input_error("sender_not_active",
                              "只有冻结活跃集合内的客户端可提交恢复份额",
                              client_id=sender)
        self._check_recovery_targets(run_id, active, dropped,
                                     set(b_shares), set(sk_shares))
        self._check_share_format(b_shares, sk_shares)
        if self._is_idempotent_replay(run_id, sender, b_shares, sk_shares):
            return {"recorded": True, "idempotent_replay": True}

        self.store.put_recovery(run_id, sender, b_shares, sk_shares)
        self._audit(run_id, "recovery_accepted",
                    f"客户端 {sender} 恢复份额已登记")
        finalized = self._maybe_finalize(run_id)
        return {"recorded": True, "idempotent_replay": False,
                "finalized": finalized}

    def _check_recovery_targets(self, run_id: str, active: set, dropped: set,
                                b_targets: set, sk_targets: set) -> None:
        overlap = b_targets & sk_targets
        if overlap:
            # 核心安全约束:同一目标不得同时索取掩码种子与私钥份额
            self._audit(
                run_id, "reject_recovery",
                f"目标 {sorted(overlap)} 同时出现掩码与私钥份额,判定越权/集合变更攻击")
            raise state_conflict(
                "mask_and_secret_overlap",
                "同一目标客户端不得同时恢复掩码种子与协商私钥",
                overlap=sorted(overlap))
        if b_targets != active or sk_targets != dropped:
            self._audit(
                run_id, "reject_recovery",
                f"恢复目标集合与冻结集合不符: b={sorted(b_targets)} "
                f"sk={sorted(sk_targets)} 期望 b={sorted(active)} sk={sorted(dropped)}")
            raise state_conflict(
                "recovery_set_mismatch",
                "恢复份额目标必须精确等于冻结的活跃/掉线集合",
                expected_b=sorted(active), expected_sk=sorted(dropped))

    @staticmethod
    def _check_share_format(b_shares: dict, sk_shares: dict) -> None:
        for target, share in list(b_shares.items()) + list(sk_shares.items()):
            if (not isinstance(share, list) or len(share) != 2
                    or not isinstance(share[0], int) or share[0] < 1):
                raise input_error("bad_share_format",
                                  "份额必须为 [正整数索引, base64]",
                                  target=target)
            _b64d(share[1])

    def _is_idempotent_replay(self, run_id: str, sender: str,
                              b_shares: dict, sk_shares: dict) -> bool:
        """重复恢复消息: 逐字节相同 -> True(幂等接受);
        内容不同 -> 状态冲突拒绝; 首次提交 -> False。"""
        import json

        existing = self.store.get_recovery(run_id, sender)
        if existing is None:
            return False
        same = (json.loads(existing["b_shares"]) == b_shares
                and json.loads(existing["sk_shares"]) == sk_shares)
        if same:
            self._audit(run_id, "recovery_replayed",
                        f"客户端 {sender} 重复提交相同恢复消息,幂等接受")
            return True
        self._audit(run_id, "reject_recovery",
                    f"客户端 {sender} 重复提交内容不同的恢复消息,拒绝")
        raise state_conflict("dup_recovery",
                             "重复恢复消息内容不一致", client_id=sender)

    def _maybe_finalize(self, run_id: str, force: bool = False) -> bool:
        sets = self.get_active_set(run_id)
        active = sets["active"]
        received = {r["sender"] for r in self.store.get_all_recovery(run_id)}
        if not force and set(active) - received:
            return False
        missing = set(active) - received
        if missing:
            self._audit(run_id, "finalize_incomplete",
                        f"强制推进时仍缺 {sorted(missing)} 的恢复消息,尝试重建")
        self._reconstruct(run_id)
        return True

    def _reconstruct(self, run_id: str) -> None:
        """重建掩码并求和。份额不足 -> 明确中止,不输出部分结果。"""
        cfg = self._config(run_id)
        sets = self.get_active_set(run_id)
        active, dropped = sets["active"], sets["dropped"]
        b_share_map, sk_share_map = self._collect_recovery_shares(
            run_id, active, dropped)

        masked = self.store.get_masked_inputs(run_id)
        total = [0] * cfg["vector_len"]
        for v in active:
            total = add_vectors_mod(total, masked[v])

        for v in active:  # 存活客户端: 去掉自掩码 PRG(b_v)
            b_v = self._reconstruct_one(
                run_id, b_share_map[v], cfg["threshold"],
                target=v, kind="掩码种子")
            total = sub_vectors_mod(
                total, crypto.expand_mask(b_v, cfg["vector_len"]))
            self._audit(run_id, "self_mask_removed",
                        f"已重建客户端 {v} 的掩码种子并去除自掩码")

        total = self._cancel_pairwise_masks(run_id, total, active, dropped,
                                            sk_share_map, cfg)
        self._store_result(run_id, total)

    def _collect_recovery_shares(self, run_id: str, active: list, dropped: list):
        """把恢复消息按目标归集成 {target: [(index, share_bytes)]}。"""
        import json

        b_map: dict[str, list[tuple[int, bytes]]] = {v: [] for v in active}
        sk_map: dict[str, list[tuple[int, bytes]]] = {w: [] for w in dropped}
        for rec in self.store.get_all_recovery(run_id):
            for target, (idx, b64) in json.loads(rec["b_shares"]).items():
                b_map[target].append((idx, _b64d(b64)))
            for target, (idx, b64) in json.loads(rec["sk_shares"]).items():
                sk_map[target].append((idx, _b64d(b64)))
        return b_map, sk_map

    def _reconstruct_one(self, run_id: str, shares: list, threshold: int,
                         target: str, kind: str) -> bytes:
        if len(shares) < threshold:
            reason = (f"客户端 {target} 的{kind}份额不足 "
                      f"({len(shares)}/{threshold}),明确中止")
            self.store.set_aborted(run_id, reason)
            self._audit(run_id, "run_aborted", reason)
            raise computation_failure("below_threshold", reason,
                                      target=target, have=len(shares),
                                      need=threshold)
        return shamir.reconstruct_secret(
            shares[:threshold], expected_len=crypto.KEY_BYTES)

    def _cancel_pairwise_masks(self, run_id: str, total: list[int],
                               active: list, dropped: list,
                               sk_share_map: dict, cfg: dict) -> list[int]:
        """掉线客户端: 重建其协商私钥, 重算并抵消其与存活者间的成对掩码。"""
        pubkeys = self.get_public_keys(run_id)
        for w in dropped:
            c_sk_w = self._reconstruct_one(
                run_id, sk_share_map[w], cfg["threshold"],
                target=w, kind="协商私钥")
            for v in active:
                seed = crypto.key_agreement(
                    c_sk_w, _b64d(pubkeys[v]["c_pk"]), crypto.HKDF_INFO_MASK)
                mask = crypto.expand_mask(seed, cfg["vector_len"])
                if w < v:  # 约定: id 小者加掩码, 大者减; 总和中只含 v 的 -m
                    total = add_vectors_mod(total, mask)
                else:      # 总和中只含 v 的 +m
                    total = sub_vectors_mod(total, mask)
            self._audit(run_id, "pairwise_mask_removed",
                        f"已重建掉线客户端 {w} 的协商私钥并抵消成对掩码")
        return total

    def _store_result(self, run_id: str, total: list[int]) -> None:
        self.store.put_result(run_id, total)
        self.store.set_done(run_id)
        self._audit(run_id, "run_done", "聚合完成,结果已写入结果表")

    def get_result(self, run_id: str) -> dict:
        row = self._run(run_id)
        if row["status"] == "ABORTED":
            return {"status": "ABORTED", "reason": row["abort_reason"]}
        if row["status"] != "DONE":
            raise state_conflict("result_not_ready", "聚合尚未完成",
                                 phase=row["phase"])
        return {"status": "DONE", "sum": self.store.get_result(run_id),
                "active": self.store.get_active_set(run_id)}
