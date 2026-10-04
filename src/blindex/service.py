"""业务层：写入、双版本等值查询（解密二次确认）、索引密钥轮换。

关键不变量：
1. 规范化先行 —— 索引与比对都作用于规范化后的值。
2. 盲索引只做候选筛选；命中必须解密后按规范化明文二次确认，
   短索引碰撞不会污染结果。
3. 轮换期间查询版本集 = 旧版本 ∪ 新版本，任何时刻不漏记录；
   重建索引可分批、可中断，状态持久化在 meta 表。
4. 日志只写记录身份与元数据（由 AuditLog 白名单强制）。
"""
from __future__ import annotations

import secrets
import uuid
from pathlib import Path

from .audit import AuditLog
from .config import Keyring, save_keyring
from .crypto_adapter import CryptoAdapter
from .errors import BlindIndexError, Category
from .protocol import FIELDS, normalize
from .storage import Storage

ROTATION_IDLE = "idle"
ROTATION_IN_PROGRESS = "in_progress"


class BlindIndexService:
    def __init__(
        self,
        storage: Storage,
        adapter: CryptoAdapter,
        audit: AuditLog,
        keyfile: str | Path | None = None,
    ):
        self._storage = storage
        self._adapter = adapter
        self._audit = audit
        self._keyfile = Path(keyfile) if keyfile else None
        self._ensure_query_versions()

    @property
    def keyring(self) -> Keyring:
        return self._adapter.keyring

    # ---- 查询版本集 ---------------------------------------------------------
    def _ensure_query_versions(self) -> list[int]:
        """查询版本集 = 已存集合 ∪ 索引表实有版本 ∪ 当前版本。

        自愈设计：即使进程在轮换中途崩溃重启，也不会漏掉任何版本。
        """
        stored = self._storage.get_query_versions()
        present = self._storage.index_versions_present()
        versions = set(stored or []) | present | {self.keyring.current_index_version}
        result = sorted(versions)
        if stored != result:
            self._storage.set_query_versions(result)
        return result

    # ---- 写入 ---------------------------------------------------------------
    def create_record(self, fields: dict, request_id: str) -> str:
        unknown = set(fields) - set(FIELDS)
        if unknown:
            raise BlindIndexError(
                Category.VALIDATION_ERROR, f"未知字段: {sorted(unknown)}"
            )
        normalized = {f: normalize(f, fields.get(f)) for f in FIELDS}
        cts = {
            f: (self._adapter.encrypt(v) if v is not None else None)
            for f, v in normalized.items()
        }
        record_id = uuid.uuid4().hex
        self._storage.insert_record(
            record_id, self.keyring.current_enc_version, cts
        )
        index_version = self.keyring.current_index_version
        for f, v in normalized.items():
            if v is not None:
                self._storage.insert_index(
                    f, index_version, self._adapter.blind_index(f, v), record_id
                )
        self._audit.log(
            "create",
            request_id,
            record_id,
            fields=sorted(f for f, v in normalized.items() if v is not None),
            versions=[index_version],
        )
        return record_id

    # ---- 等值查询（双版本 + 解密二次确认） ------------------------------------
    def query(self, field: str, value: str | None, request_id: str) -> dict:
        norm = normalize(field, value)  # 未知字段在此抛 VALIDATION_ERROR
        if norm is None:
            # NULL 不入索引，对 NULL 的等值查询按规范拒绝并单列失败类别
            self._audit.log(
                "query", request_id, field=field, status="rejected",
                category=Category.NULL_QUERY.value,
            )
            return {
                "status": "rejected",
                "category": Category.NULL_QUERY.value,
                "field": field,
                "matched_record_ids": [],
                "searched_index_versions": [],
                "candidates": 0,
                "rejected_candidates": 0,
                "uncertain": [],
            }

        versions = self._ensure_query_versions()
        index_hexes = [self._adapter.blind_index(field, norm, v) for v in versions]
        candidates = self._storage.find_candidates(field, index_hexes)

        confirmed: list[str] = []
        rejected = 0
        uncertain: list[dict] = []
        for rid in candidates:
            rec = self._storage.get_record(rid)
            blob = rec[field] if rec else None
            if blob is None:
                continue  # 索引残留但记录/字段缺失：不计入也不报错
            try:
                plaintext = self._adapter.decrypt(blob)
            except BlindIndexError as exc:
                # 解密失败不能静默丢弃：单列不确定结论与失败类别
                uncertain.append({"record_id": rid, "category": exc.category.value})
                continue
            if normalize(field, plaintext) == norm:
                confirmed.append(rid)
            else:
                rejected += 1  # 盲索引碰撞，被二次确认排除

        self._audit.log(
            "query", request_id, field=field, versions=versions,
            candidates=len(candidates), confirmed=len(confirmed),
            rejected=rejected, uncertain=len(uncertain),
        )
        return {
            "status": "ok",
            "category": None,
            "field": field,
            "matched_record_ids": sorted(confirmed),
            "searched_index_versions": versions,
            "candidates": len(candidates),
            "rejected_candidates": rejected,
            "uncertain": uncertain,
        }

    # ---- 读取（本地演示用途：解密返回明文） ------------------------------------
    def get_record(self, record_id: str, request_id: str) -> dict:
        rec = self._storage.get_record(record_id)
        if rec is None:
            raise BlindIndexError(Category.NOT_FOUND, f"记录不存在: {record_id}")
        out = {"record_id": record_id, "enc_version": rec["enc_version"]}
        for f in FIELDS:
            out[f] = self._adapter.decrypt(rec[f]) if rec[f] is not None else None
        self._audit.log("read", request_id, record_id)
        return out

    # ---- 索引密钥轮换 ---------------------------------------------------------
    def start_index_rotation(self, request_id: str) -> dict:
        state = self._storage.get_rotation_state()
        if state == ROTATION_IN_PROGRESS:
            raise BlindIndexError(
                Category.ROTATION_STATE_ERROR, "轮换已在进行中，请先完成重建索引"
            )
        kr = self.keyring
        old_version = kr.current_index_version
        new_version = max(kr.index_keys) + 1
        kr.index_keys[new_version] = secrets.token_bytes(32)
        kr.current_index_version = new_version
        kr.validate()
        if self._keyfile:
            save_keyring(kr, self._keyfile)

        # 双版本查询：旧版本保留在查询集合中，直到重建完成
        versions = sorted(set(self._ensure_query_versions()) | {new_version})
        self._storage.set_query_versions(versions)
        self._storage.set_rotation_state(ROTATION_IN_PROGRESS)
        self._audit.log(
            "rotate", request_id, from_version=old_version,
            to_version=new_version, state=ROTATION_IN_PROGRESS,
        )
        return {
            "from_version": old_version,
            "to_version": new_version,
            "query_versions": versions,
            "state": ROTATION_IN_PROGRESS,
        }

    def reindex_batch(self, limit: int, request_id: str) -> dict:
        """分批重建索引，可中断。每批处理 limit 条记录后返回剩余量。"""
        if self._storage.get_rotation_state() != ROTATION_IN_PROGRESS:
            raise BlindIndexError(
                Category.ROTATION_STATE_ERROR, "当前不在轮换中，无需重建索引"
            )
        if limit < 1:
            raise BlindIndexError(Category.VALIDATION_ERROR, "limit 必须 ≥ 1")
        current = self.keyring.current_index_version
        pending = self._pending_records(current)
        processed = 0
        for rec in pending[:limit]:
            for f in FIELDS:
                blob = rec[f]
                if blob is None or self._storage.has_index(f, current, rec["record_id"]):
                    continue
                norm = normalize(f, self._adapter.decrypt(blob))
                self._storage.insert_index(
                    f, current, self._adapter.blind_index(f, norm), rec["record_id"]
                )
            processed += 1

        remaining = len(self._pending_records(current))
        state = ROTATION_IN_PROGRESS
        if remaining == 0:
            # 重建完成：删除旧版本索引，查询集合收敛到当前版本
            old_versions = [
                v for v in self._storage.get_query_versions() if v != current
            ]
            self._storage.delete_indexes_for_versions(old_versions)
            self._storage.set_query_versions([current])
            self._storage.set_rotation_state(ROTATION_IDLE)
            state = ROTATION_IDLE
        self._audit.log(
            "reindex", request_id, processed=processed,
            remaining=remaining, state=state,
        )
        return {
            "processed": processed,
            "remaining": remaining,
            "state": state,
            "query_versions": self._storage.get_query_versions(),
        }

    def _pending_records(self, current_version: int) -> list[dict]:
        """仍存在字段缺少当前版本索引的记录（本地规模，全量扫描）。"""
        pending = []
        for rec in self._storage.iter_records():
            for f in FIELDS:
                if rec[f] is not None and not self._storage.has_index(
                    f, current_version, rec["record_id"]
                ):
                    pending.append(rec)
                    break
        return pending

    def rotation_status(self) -> dict:
        return {
            "state": self._storage.get_rotation_state(),
            "query_versions": self._ensure_query_versions(),
            "current_index_version": self.keyring.current_index_version,
            "index_versions_present": sorted(self._storage.index_versions_present()),
        }
