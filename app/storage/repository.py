"""仓储层: 知识库内容与推理运行记录的持久化操作。

所有写入方法假设调用方 (service) 已经通过规则语言校验与内核一致性检查;
仓储层只负责结构化存储与重复键等状态约束。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from ..errors import KnowledgeStateError
from ..rulelang.ast_nodes import Literal, Rule, Theory
from ..rulelang.codecs import (
    ground_key,
    literal_to_dict,
    rule_from_dict,
)
from .database import dumps


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------ #
    # 知识库
    # ------------------------------------------------------------------ #

    def create_kb(self, kb_id: str, name: str) -> None:
        existing = self.conn.execute(
            "SELECT kb_id FROM knowledge_bases WHERE kb_id = ?", (kb_id,)
        ).fetchone()
        if existing is not None:
            raise KnowledgeStateError(
                f"知识库 {kb_id!r} 已存在",
                details={"kb_id": kb_id},
            )
        now = _utc_now()
        self.conn.execute(
            "INSERT INTO knowledge_bases (kb_id, name, version, created_at, updated_at)"
            " VALUES (?, ?, 1, ?, ?)",
            (kb_id, name, now, now),
        )
        self.conn.commit()

    def require_kb(self, kb_id: str) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM knowledge_bases WHERE kb_id = ?", (kb_id,)
        ).fetchone()
        if row is None:
            raise KnowledgeStateError(
                f"知识库 {kb_id!r} 不存在",
                details={"kb_id": kb_id},
            )
        return row

    def list_kbs(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT kb_id, name, version, created_at, updated_at, stats_json"
            " FROM knowledge_bases ORDER BY created_at"
        ).fetchall()
        return [
            {
                "kb_id": row["kb_id"],
                "name": row["name"],
                "version": row["version"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "stats": json.loads(row["stats_json"]),
            }
            for row in rows
        ]

    def delete_kb(self, kb_id: str) -> None:
        self.require_kb(kb_id)
        self.conn.execute("DELETE FROM knowledge_bases WHERE kb_id = ?", (kb_id,))
        self.conn.commit()

    def touch(self, kb_id: str, stats: dict) -> None:
        self.conn.execute(
            "UPDATE knowledge_bases SET version = version + 1, updated_at = ?, stats_json = ?"
            " WHERE kb_id = ?",
            (_utc_now(), dumps(stats), kb_id),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # 整段理论装载
    # ------------------------------------------------------------------ #

    def replace_theory(self, kb_id: str, theory: Theory) -> None:
        """在一个事务内清空并写入整段理论 (事实/规则/优先关系)。"""
        self.require_kb(kb_id)
        try:
            with self.conn:
                self.conn.execute("DELETE FROM priorities WHERE kb_id = ?", (kb_id,))
                self.conn.execute("DELETE FROM rules WHERE kb_id = ?", (kb_id,))
                self.conn.execute("DELETE FROM facts WHERE kb_id = ?", (kb_id,))
                for fact in theory.facts:
                    self._insert_fact_row(kb_id, fact)
                for rule in theory.all_rules:
                    self._insert_rule_row(kb_id, rule)
                for strong, weak in theory.priorities:
                    self.conn.execute(
                        "INSERT INTO priorities (kb_id, strong_id, weak_id) VALUES (?, ?, ?)",
                        (kb_id, strong, weak),
                    )
        except sqlite3.IntegrityError as exc:
            raise KnowledgeStateError(
                f"写入理论时违反存储约束: {exc}",
                details={"kb_id": kb_id},
            ) from exc

    def add_facts(self, kb_id: str, facts: list[Literal]) -> int:
        self.require_kb(kb_id)
        inserted = 0
        try:
            with self.conn:
                for fact in facts:
                    inserted += self._insert_fact_row(kb_id, fact)
        except sqlite3.IntegrityError as exc:
            raise KnowledgeStateError(
                f"写入事实时违反存储约束 (可能与现有证据冲突或重复): {exc}",
                details={"kb_id": kb_id},
            ) from exc
        return inserted

    def _insert_fact_row(self, kb_id: str, lit: Literal) -> int:
        predicate, sign, args_json = ground_key(lit)
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO facts (kb_id, predicate, sign, args_json, text)"
            " VALUES (?, ?, ?, ?, ?)",
            (kb_id, predicate, sign, args_json, lit.render()),
        )
        return cursor.rowcount

    def _insert_rule_row(self, kb_id: str, rule: Rule) -> None:
        self.conn.execute(
            "INSERT INTO rules (kb_id, rule_id, kind, head_json, body_json, rule_text, line)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                kb_id,
                rule.rule_id,
                rule.kind.value,
                dumps(literal_to_dict(rule.head)),
                dumps([literal_to_dict(b) for b in rule.body]),
                rule.render(),
                rule.line,
            ),
        )

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #

    def load_theory(self, kb_id: str) -> Theory:
        self.require_kb(kb_id)
        theory = Theory()

        fact_rows = self.conn.execute(
            "SELECT predicate, sign, args_json FROM facts WHERE kb_id = ?",
            (kb_id,),
        ).fetchall()
        for row in fact_rows:
            args = [("c", value) for value in json.loads(row["args_json"])]
            theory.facts.append(
                Literal(
                    predicate=row["predicate"],
                    args=tuple(args),
                    negated=row["sign"] == "-",
                )
            )

        rule_rows = self.conn.execute(
            "SELECT rule_id, kind, head_json, body_json, line FROM rules WHERE kb_id = ?"
            " ORDER BY kind, rule_id",
            (kb_id,),
        ).fetchall()
        for row in rule_rows:
            rule = rule_from_dict(
                {
                    "rule_id": row["rule_id"],
                    "kind": row["kind"],
                    "head": json.loads(row["head_json"]),
                    "body": json.loads(row["body_json"]),
                    "line": row["line"],
                }
            )
            if rule.is_strict:
                theory.strict_rules.append(rule)
            else:
                theory.defeasible_rules.append(rule)

        priority_rows = self.conn.execute(
            "SELECT strong_id, weak_id FROM priorities WHERE kb_id = ?",
            (kb_id,),
        ).fetchall()
        theory.priorities = [(r["strong_id"], r["weak_id"]) for r in priority_rows]

        return theory

    def load_rules(self, kb_id: str) -> list[Rule]:
        rows = self.conn.execute(
            "SELECT rule_id, kind, head_json, body_json, line FROM rules"
            " WHERE kb_id = ? ORDER BY kind, rule_id",
            (kb_id,),
        ).fetchall()
        rules = []
        for row in rows:
            data = {
                "rule_id": row["rule_id"],
                "kind": row["kind"],
                "head": json.loads(row["head_json"]),
                "body": json.loads(row["body_json"]),
                "line": row["line"],
            }
            rules.append(rule_from_dict(data))
        return rules

    def load_priorities(self, kb_id: str) -> list[tuple[str, str]]:
        rows = self.conn.execute(
            "SELECT strong_id, weak_id FROM priorities WHERE kb_id = ?"
            " ORDER BY strong_id, weak_id",
            (kb_id,),
        ).fetchall()
        return [(row["strong_id"], row["weak_id"]) for row in rows]

    def load_facts(self, kb_id: str) -> list[Literal]:
        self.require_kb(kb_id)
        rows = self.conn.execute(
            "SELECT predicate, sign, args_json FROM facts"
            " WHERE kb_id = ? ORDER BY predicate, sign, args_json",
            (kb_id,),
        ).fetchall()
        result = []
        for row in rows:
            args = tuple(("c", value) for value in json.loads(row["args_json"]))
            result.append(
                Literal(
                    predicate=row["predicate"],
                    args=args,
                    negated=row["sign"] == "-",
                )
            )
        return result

    # ------------------------------------------------------------------ #
    # 运行记录 (问题回放)
    # ------------------------------------------------------------------ #

    def insert_run(
        self,
        run_id: str,
        endpoint: str,
        request: dict,
        kb_id: Optional[str] = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO runs (run_id, kb_id, started_at, endpoint, request_json)"
            " VALUES (?, ?, ?, ?, ?)",
            (run_id, kb_id, _utc_now(), endpoint, dumps(request)),
        )
        self.conn.commit()

    def finish_run(
        self,
        run_id: str,
        *,
        status_summary: Optional[str],
        result: Optional[dict],
        error: Optional[dict],
    ) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at = ?, status_summary = ?, result_json = ?,"
            " error_category = ?, error_name = ?, error_message = ?, details_json = ?"
            " WHERE run_id = ?",
            (
                _utc_now(),
                status_summary,
                dumps(result) if result is not None else None,
                error.get("category") if error else None,
                error.get("error") if error else None,
                error.get("message") if error else None,
                dumps(error.get("details")) if error else None,
                run_id,
            ),
        )
        self.conn.commit()

    def get_run(self, run_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "kb_id": row["kb_id"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "endpoint": row["endpoint"],
            "request": json.loads(row["request_json"]),
            "status_summary": row["status_summary"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error_category": row["error_category"],
            "error_name": row["error_name"],
            "error_message": row["error_message"],
            "error_details": json.loads(row["details_json"]) if row["details_json"] else None,
        }

    def list_recent_runs(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT run_id, kb_id, started_at, endpoint, status_summary,"
            " error_category, error_name FROM runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
