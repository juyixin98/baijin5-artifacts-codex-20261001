"""业务编排层:批次生命周期、提交校验、同态聚合、解密与独立验证。

信任假设(详见 README):
- 聚合方只持有公钥即可完成的操作:提交校验、聚合。
- 本地测试模式下私钥(Fernet 加密)与聚合方同库,解密接口由此进程执行;
  真实部署中解密方应是独立角色。
- 参与者需诚实声明 |x|(declared_abs),服务据此维护最坏情况总和上界;
  不诚实声明只能在解码阶段被 OVERFLOW_DETECTED 发现,无法事前阻止。
"""
from __future__ import annotations

import threading
import uuid
import warnings
from typing import Optional

from cryptography.fernet import Fernet
from phe.paillier import PaillierPublicKey

from . import crypto_adapter as crypto
from . import encoding
from . import verifier
from .config import Settings
from .errors import ErrorCategory, ServiceError
from .store import Store


class AggregationService:
    def __init__(self, settings: Settings, store: Optional[Store] = None,
                 run_id: Optional[str] = None) -> None:
        self.settings = settings
        self.store = store or Store(settings.db_path)
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self._lock = threading.Lock()
        if settings.fernet_key:
            self._fernet = Fernet(settings.fernet_key.encode("ascii"))
        else:
            self._fernet = Fernet(Fernet.generate_key())
            warnings.warn(
                "未配置 PAGG_FERNET_KEY,私钥落盘密钥为进程内临时生成,"
                "重启后历史批次不可解密(仅本地测试可接受)",
                stacklevel=2,
            )

    # ---- 批次 ----
    def create_batch(self, label: Optional[str] = None,
                     sum_bound: Optional[int] = None) -> dict:
        pub, priv = crypto.generate_keypair(self.settings.key_size)
        n = pub.n
        bound = sum_bound if sum_bound is not None else n // 4
        if not 0 < bound < n // 2:
            raise ServiceError(
                ErrorCategory.VALIDATION,
                f"sum_bound 必须满足 0 < B < n/2(当前 n/2={n // 2})",
            )
        batch_id = uuid.uuid4().hex[:16]
        self.store.insert_batch({
            "batch_id": batch_id,
            "label": label,
            "n": str(n),
            "fingerprint": crypto.key_fingerprint(n),
            "sum_bound": str(bound),
            "max_plaintext_abs": str(self.settings.max_plaintext_abs),
            "max_weight_abs": str(self.settings.max_weight_abs),
            "privkey_enc": crypto.export_private_key(priv, self._fernet),
        })
        self.store.audit(self.run_id, batch_id, "BATCH_CREATED", {
            "label": label, "key_size": self.settings.key_size,
            "sum_bound": str(bound),
            "max_plaintext_abs": str(self.settings.max_plaintext_abs),
            "max_weight_abs": str(self.settings.max_weight_abs),
        })
        return self.get_batch(batch_id)

    def get_batch(self, batch_id: str) -> dict:
        row = self._batch_row(batch_id)
        return {
            "batch_id": row["batch_id"],
            "label": row["label"],
            "n": row["n"],
            "key_fingerprint": row["fingerprint"],
            "sum_bound": row["sum_bound"],
            "used_bound": row["used_bound"],
            "max_plaintext_abs": row["max_plaintext_abs"],
            "max_weight_abs": row["max_weight_abs"],
            "created_at": row["created_at"],
        }

    # ---- 提交 ----
    def submit(self, batch_id: str, participant_id: str, c: int, exponent: int,
               weight: int, key_fingerprint: str, declared_abs: int) -> dict:
        row = self._batch_row(batch_id)
        if key_fingerprint != row["fingerprint"]:
            self.store.audit(self.run_id, batch_id, "SUBMISSION_REJECTED", {
                "participant_id": participant_id, "reason": ErrorCategory.KEY_MISMATCH.value,
            })
            raise ServiceError(
                ErrorCategory.KEY_MISMATCH,
                "密钥指纹与批次绑定不一致,拒绝混入其他密钥的密文",
            )
        encoding.validate_weight(weight, self.settings.max_weight_abs)
        if not 0 <= declared_abs <= self.settings.max_plaintext_abs:
            raise ServiceError(
                ErrorCategory.OUT_OF_RANGE,
                f"声明明文绝对值 {declared_abs} 不在 [0, "
                f"{self.settings.max_plaintext_abs}] 内",
            )
        pub = PaillierPublicKey(int(row["n"]))
        enc = crypto.deserialize(pub, c, exponent)  # 指数/密文范围校验在适配层

        risk = declared_abs * abs(weight)
        with self._lock:
            used = int(row["used_bound"])
            if used + risk > int(row["sum_bound"]):
                self.store.audit(self.run_id, batch_id, "SUBMISSION_REJECTED", {
                    "participant_id": participant_id,
                    "reason": ErrorCategory.OVERFLOW_RISK.value,
                    "used_bound": str(used), "risk": str(risk),
                    "sum_bound": row["sum_bound"],
                })
                raise ServiceError(
                    ErrorCategory.OVERFLOW_RISK,
                    "接受该提交将使最坏情况加权和突破批次上界,已拒绝",
                    detail={"used_bound": str(used), "risk": str(risk),
                            "sum_bound": row["sum_bound"]},
                )
            sub_id = self.store.insert_submission({
                "batch_id": batch_id, "participant_id": participant_id,
                "c": str(c), "exponent": exponent, "weight": str(weight),
                "declared_abs": str(declared_abs),
            })
            new_used = self.store.add_used_bound(batch_id, risk)
        self.store.audit(self.run_id, batch_id, "SUBMISSION_ACCEPTED", {
            "sub_id": sub_id, "participant_id": participant_id,
            "weight": str(weight), "declared_abs": str(declared_abs),
            "used_bound": str(new_used),
        })
        return {"sub_id": sub_id, "used_bound": str(new_used)}

    # ---- 聚合 ----
    def aggregate(self, batch_id: str) -> dict:
        row = self._batch_row(batch_id)
        subs = self.store.list_submissions(batch_id)
        if not subs:
            raise ServiceError(ErrorCategory.VALIDATION, "批次内没有可聚合的提交")
        pub = PaillierPublicKey(int(row["n"]))
        acc = crypto.encrypt_signed(pub, 0)
        for sub in subs:
            enc = crypto.deserialize(pub, int(sub["c"]), sub["exponent"])
            acc = crypto.add(acc, crypto.scalar_mul(enc, int(sub["weight"])))
        agg_id = self.store.insert_aggregate(
            batch_id, str(acc.ciphertext(be_secure=False)), len(subs))
        self.store.audit(self.run_id, batch_id, "AGGREGATE_COMPUTED", {
            "agg_id": agg_id, "submission_count": len(subs),
            "used_bound": row["used_bound"], "sum_bound": row["sum_bound"],
        })
        return {"agg_id": agg_id, "c": str(acc.ciphertext(be_secure=False)),
                "exponent": 0, "submission_count": len(subs)}

    # ---- 解密 ----
    def decrypt(self, batch_id: str) -> dict:
        row = self._batch_row(batch_id)
        agg = self.store.latest_aggregate(batch_id)
        if agg is None:
            raise ServiceError(ErrorCategory.NOT_FOUND, "尚无聚合产物,请先调用聚合")
        pub = PaillierPublicKey(int(row["n"]))
        priv = crypto.import_private_key(pub, row["privkey_enc"], self._fernet)
        enc = crypto.deserialize(pub, int(agg["c"]), 0)
        residue = crypto.decrypt_residue(priv, enc)
        try:
            value = encoding.decode_signed(residue, pub.n, int(row["sum_bound"]))
        except ServiceError as exc:
            self.store.audit(self.run_id, batch_id, "DECRYPT_OVERFLOW", {
                "agg_id": agg["agg_id"], "category": exc.category.value,
                "detail": exc.detail,
            })
            raise
        self.store.audit(self.run_id, batch_id, "DECRYPT_OK", {
            "agg_id": agg["agg_id"], "value": str(value),
        })
        return {"agg_id": agg["agg_id"], "value": str(value)}

    # ---- 独立验证 ----
    def verify(self, batch_id: str,
               reference: list[tuple[str, int]]) -> dict:
        """用调用方提供的明文参考(参与者 -> 原始值)比对解密结果。

        权重取自服务端存证;参考明文由调用方给出,不由本服务反推。
        """
        row = self._batch_row(batch_id)
        subs = self.store.list_submissions(batch_id)
        weight_by_participant = {s["participant_id"]: int(s["weight"]) for s in subs}
        pairs = []
        for participant_id, value in reference:
            if participant_id not in weight_by_participant:
                raise ServiceError(
                    ErrorCategory.VALIDATION,
                    f"参考中的参与者 {participant_id!r} 没有对应提交",
                )
            pairs.append((value, weight_by_participant[participant_id]))
        ref_sum = verifier.reference_weighted_sum(pairs)
        bound = int(row["sum_bound"])
        try:
            decrypted: Optional[int] = int(self.decrypt(batch_id)["value"])
        except ServiceError as exc:
            if exc.category is not ErrorCategory.OVERFLOW_DETECTED:
                raise
            decrypted = None
        result = verifier.judge(decrypted, ref_sum, bound)
        self.store.audit(self.run_id, batch_id, f"VERIFY_{result['verdict']}", {
            "expected": result["expected"], "actual": result["actual"],
            "reason": result["reason"],
        })
        return result

    # ---- 审计 ----
    def list_audit(self, batch_id: str) -> list[dict]:
        self._batch_row(batch_id)
        return self.store.list_audit(batch_id)

    # ---- 内部 ----
    def _batch_row(self, batch_id: str) -> dict:
        row = self.store.get_batch(batch_id)
        if row is None:
            raise ServiceError(ErrorCategory.NOT_FOUND,
                               f"批次 {batch_id!r} 不存在")
        return row
