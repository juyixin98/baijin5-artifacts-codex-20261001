"""客户端协议逻辑:密钥生成、份额分发、掩码输入、恢复响应.

客户端通过 Transport 接口与服务器通信(测试中为本地 ASGI 传输),
自身不持有服务器内部状态。客户端侧同样强制执行安全约束:

- 轮3 只响应与本地视图一致的冻结活跃集合;集合对不上即中止。
- 对同一目标绝不同时交出掩码种子份额与私钥份额。
- 恢复消息只提交一次;重复提交内容必须逐字节一致(幂等重放)。
"""

from __future__ import annotations

import base64
import json
import os
from typing import Protocol

from . import crypto_adapters as crypto
from . import shamir
from .encoding import (
    EncodingParams,
    add_vectors_mod,
    encode_vector,
    sub_vectors_mod,
)
from .errors import state_conflict


class Transport(Protocol):
    """最小传输接口:POST/GET JSON,错误时抛 SecAggError。"""

    def post(self, path: str, payload: dict) -> dict: ...

    def get(self, path: str) -> dict: ...


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


class SecAggClient:
    """单个客户端的协议状态机。掉线 = 测试不再调用其后续轮次方法。"""

    def __init__(self, client_id: str, run_id: str, transport: Transport,
                 params: EncodingParams, threshold: int, vector_len: int) -> None:
        self.client_id = client_id
        self.run_id = run_id
        self.transport = transport
        self.params = params
        self.threshold = threshold
        self.vector_len = vector_len

        self.c_sk, self.c_pk = crypto.generate_keypair()
        self.s_sk, self.s_pk = crypto.generate_keypair()
        self.b_seed = os.urandom(crypto.KEY_BYTES)

        self._peers: list[str] = []            # 轮1观察到的参与者(有序)
        self._own_index: int = 0
        self._own_b_share: tuple[int, bytes] | None = None
        self._own_sk_share: tuple[int, bytes] | None = None
        self._b_shares_of: dict[str, tuple[int, bytes]] = {}   # sender -> 其 b 的份额
        self._sk_shares_of: dict[str, tuple[int, bytes]] = {}  # sender -> 其 c_SK 的份额
        self._frozen_active: list[str] | None = None
        self._recovery_sent: dict | None = None

    # ---- 轮0 ----

    def advertise_keys(self) -> dict:
        return self.transport.post(
            f"/runs/{self.run_id}/keys",
            {"client_id": self.client_id,
             "c_pk": _b64e(self.c_pk), "s_pk": _b64e(self.s_pk)},
        )

    # ---- 轮1 ----

    def share_keys(self) -> dict:
        pubkeys = self.transport.get(f"/runs/{self.run_id}/public_keys")
        peers = sorted(pubkeys.keys())
        if self.client_id not in peers:
            raise state_conflict("not_registered", "服务器端找不到本客户端的密钥")
        self._peers = peers
        n = len(peers)
        self._own_index = peers.index(self.client_id) + 1

        b_shares = shamir.split_secret(self.b_seed, self.threshold, n)
        sk_shares = shamir.split_secret(self.c_sk, self.threshold, n)
        self._own_b_share = b_shares[self._own_index - 1]
        self._own_sk_share = sk_shares[self._own_index - 1]

        ciphertexts: dict[str, str] = {}
        for peer in peers:
            if peer == self.client_id:
                continue
            idx = peers.index(peer) + 1
            enc_key = crypto.key_agreement(
                self.s_sk, _b64d(pubkeys[peer]["s_pk"]),
                crypto.HKDF_INFO_SHARE_ENC)
            payload = json.dumps({
                "b_share": [b_shares[idx - 1][0], _b64e(b_shares[idx - 1][1])],
                "sk_share": [sk_shares[idx - 1][0], _b64e(sk_shares[idx - 1][1])],
            }).encode()
            aad = f"{self.run_id}|{self.client_id}|{peer}".encode()
            ciphertexts[peer] = _b64e(crypto.aead_encrypt(enc_key, payload, aad))
        return self.transport.post(
            f"/runs/{self.run_id}/shares",
            {"client_id": self.client_id, "ciphertexts": ciphertexts},
        )

    # ---- 轮2 ----

    def submit_masked_input(self, vector: list[float]) -> dict:
        inbox = self.transport.get(
            f"/runs/{self.run_id}/shares/{self.client_id}")
        pubkeys = self.transport.get(f"/runs/{self.run_id}/public_keys")

        senders = sorted(inbox.keys())
        for sender in senders:
            enc_key = crypto.key_agreement(
                self.s_sk, _b64d(pubkeys[sender]["s_pk"]),
                crypto.HKDF_INFO_SHARE_ENC)
            aad = f"{self.run_id}|{sender}|{self.client_id}".encode()
            payload = json.loads(crypto.aead_decrypt(
                enc_key, _b64d(inbox[sender]), aad))
            self._b_shares_of[sender] = (
                payload["b_share"][0], _b64d(payload["b_share"][1]))
            self._sk_shares_of[sender] = (
                payload["sk_share"][0], _b64d(payload["sk_share"][1]))
        # 参与者集合 = 轮1实际发送份额的客户端 + 自己(与服务器 share_senders 一致)
        self._peers = sorted(set(senders) | {self.client_id})

        y = encode_vector(vector, self.params)
        y = add_vectors_mod(y, crypto.expand_mask(self.b_seed, self.vector_len))
        for peer in self._peers:
            if peer == self.client_id:
                continue
            seed = crypto.key_agreement(
                self.c_sk, _b64d(pubkeys[peer]["c_pk"]), crypto.HKDF_INFO_MASK)
            mask = crypto.expand_mask(seed, self.vector_len)
            if self.client_id < peer:
                y = add_vectors_mod(y, mask)
            else:
                y = sub_vectors_mod(y, mask)
        return self.transport.post(
            f"/runs/{self.run_id}/masked",
            {"client_id": self.client_id, "vector": y},
        )

    # ---- 轮3 ----

    def send_recovery(self) -> dict:
        sets = self.transport.get(f"/runs/{self.run_id}/active_set")
        active, dropped = sorted(sets["active"]), sorted(sets["dropped"])
        self._check_frozen_view(active, dropped)

        b_out: dict[str, list] = {}
        sk_out: dict[str, list] = {}
        for v in active:
            share = (self._own_b_share if v == self.client_id
                     else self._b_shares_of.get(v))
            if share is None:
                raise state_conflict(
                    "missing_share", f"本地没有目标 {v} 的掩码种子份额")
            b_out[v] = [share[0], _b64e(share[1])]
        for w in dropped:
            share = (self._own_sk_share if w == self.client_id
                     else self._sk_shares_of.get(w))
            if share is None:
                raise state_conflict(
                    "missing_share", f"本地没有目标 {w} 的私钥份额")
            sk_out[w] = [share[0], _b64e(share[1])]

        # 不变量自检: 同一目标绝不同时交出两类份额
        if set(b_out) & set(sk_out):
            raise state_conflict(
                "mask_and_secret_overlap",
                "客户端拒绝同时交出同一目标的掩码与私钥份额")

        message = {"client_id": self.client_id,
                   "b_shares": b_out, "sk_shares": sk_out}
        if self._recovery_sent is not None:
            if self._recovery_sent != message:
                raise state_conflict(
                    "dup_recovery_local",
                    "本地已发送过内容不同的恢复消息,拒绝再次发送")
        self._recovery_sent = message
        return self.transport.post(f"/runs/{self.run_id}/recovery", message)

    def _check_frozen_view(self, active: list[str], dropped: list[str]) -> None:
        """客户端侧的集合变更防御:冻结集合必须与本地轮1视图一致。"""
        if set(active) & set(dropped):
            raise state_conflict(
                "set_overlap", "服务器给出的活跃/掉线集合相交,疑似集合变更攻击")
        if set(active) | set(dropped) != set(self._peers):
            raise state_conflict(
                "set_changed",
                "冻结集合与本地轮1参与者视图不符,疑似集合变更攻击",
                local_peers=self._peers, active=active, dropped=dropped)
        if len(active) < self.threshold:
            raise state_conflict(
                "below_threshold", "存活客户端数低于门限,客户端拒绝继续",
                active=len(active), threshold=self.threshold)
        if self._frozen_active is not None and self._frozen_active != active:
            raise state_conflict(
                "active_set_changed",
                "服务器在恢复阶段变更了活跃集合,拒绝响应",
                previous=self._frozen_active, current=active)
        self._frozen_active = active

    # ---- 工具 ----

    @staticmethod
    def decode_sum(elements: list[int], params: EncodingParams) -> list[float]:
        from .encoding import decode_vector

        return decode_vector(elements, params)
