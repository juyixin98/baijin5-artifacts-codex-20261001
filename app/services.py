"""应用服务层: 连接存储、内核与日志, 是 API 唯一依赖的业务入口。"""
from __future__ import annotations

import uuid
from typing import Optional

from .errors import (
    ReasonerError,
    ResourceLimitError,
)
from .kernel.facade import (
    CompiledKB,
    answer_query,
    answer_to_dict,
    compile_knowledge_base,
)
from .rulelang.dsl import parse_fact, parse_query, parse_theory
from .storage.repository import KnowledgeRepository
from .storage.runlog import RunLogger


class ReasonerService:
    def __init__(
        self,
        repo: KnowledgeRepository,
        logger: RunLogger,
        limits,
    ) -> None:
        self.repo = repo
        self.logger = logger
        self.limits = limits

    @staticmethod
    def new_run_id() -> str:
        return "run-" + uuid.uuid4().hex[:16]

    # ------------------------------------------------------------------ #
    # 知识库管理
    # ------------------------------------------------------------------ #

    def create_kb(self, kb_id: str, name: str) -> dict:
        self.repo.create_kb(kb_id, name)
        return {"kb_id": kb_id, "name": name}

    def load_theory(self, kb_id: str, theory_text: str) -> dict:
        run_id = self.new_run_id()
        request = {"kb_id": kb_id, "theory": theory_text}
        self.logger.log_request_start(run_id, "kb.load_theory", request)
        self.repo.insert_run(run_id, "kb.load_theory", request, kb_id=kb_id)
        try:
            self._check_input_size(theory_text)
            theory = parse_theory(theory_text)  # 词法/语法/静态语义/无环校验

            self.repo.require_kb(kb_id)

            # 先在内存中编译 (接地 + 严格一致性 + grounded 求值),
            # 严格矛盾等状态冲突在落库之前拦截, 不污染已持久化状态。
            compiled = compile_knowledge_base(
                theory.facts, theory.all_rules, theory.priorities, self.limits
            )
            stats = compiled.stats()
            self.logger.log_kernel_trace(run_id, "compiled", stats)

            self.repo.replace_theory(kb_id, theory)
            self.repo.touch(kb_id, stats)

            result = {"kb_id": kb_id, "stats": stats}
            self.repo.finish_run(run_id, status_summary="ok", result=result, error=None)
            self.logger.log_request_finish(
                run_id, "kb.load_theory", request, {"outcome": "ok"}, result
            )
            return result
        except ReasonerError as exc:
            self._record_failure(run_id, "kb.load_theory", request, exc)
            raise

    def add_facts(self, kb_id: str, fact_texts: list[str]) -> dict:
        run_id = self.new_run_id()
        request = {"kb_id": kb_id, "facts": fact_texts}
        self.logger.log_request_start(run_id, "kb.add_facts", request)
        self.repo.insert_run(run_id, "kb.add_facts", request, kb_id=kb_id)
        try:
            if len(fact_texts) > self.limits.max_input_facts:
                raise ResourceLimitError(
                    "输入事实数量超过上限",
                    details={
                        "count": len(fact_texts),
                        "max_input_facts": self.limits.max_input_facts,
                    },
                )
            facts = [parse_fact(text) for text in fact_texts]
            inserted = self.repo.add_facts(kb_id, facts)

            compiled = self._compile(kb_id, run_id)
            self.repo.touch(kb_id, compiled.stats())

            result = {"kb_id": kb_id, "inserted": inserted, "stats": compiled.stats()}
            self.repo.finish_run(run_id, status_summary="ok", result=result, error=None)
            self.logger.log_request_finish(
                run_id, "kb.add_facts", request, {"outcome": "ok"}, result
            )
            return result
        except ReasonerError as exc:
            self._record_failure(run_id, "kb.add_facts", request, exc)
            raise

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #

    def query(self, kb_id: str, query_text: str) -> dict:
        run_id = self.new_run_id()
        request = {"kb_id": kb_id, "query": query_text}
        self.logger.log_request_start(run_id, "reasoner.query", request)
        self.repo.insert_run(run_id, "reasoner.query", request, kb_id=kb_id)
        try:
            query_lit = parse_query(query_text)
            compiled = self._compile(kb_id, run_id)
            answers = answer_query(
                compiled, query_lit, max_chains=self.limits.max_chains_per_goal
            )
            payload = {
                "run_id": run_id,
                "kb_id": kb_id,
                "query": query_text,
                "answers": [answer_to_dict(a) for a in answers],
                "kb_stats": compiled.stats(),
            }
            statuses = [a.status for a in answers]
            summary = {
                "outcome": "ok",
                "answers": len(answers),
                "statuses": statuses,
            }
            self.repo.finish_run(run_id, status_summary="ok", result=payload, error=None)
            self.logger.log_kernel_trace(
                run_id,
                "query_answers",
                {
                    "query": query_text,
                    "answers": [
                        {
                            "goal": a.goal,
                            "substitution": a.substitution,
                            "status": a.status,
                            "reason_code": a.reason_code,
                        }
                        for a in answers
                    ],
                },
            )
            self.logger.log_request_finish(
                run_id, "reasoner.query", request, summary, payload
            )
            return payload
        except ReasonerError as exc:
            self._record_failure(run_id, "reasoner.query", request, exc)
            raise

    def get_run(self, run_id: str) -> Optional[dict]:
        return self.repo.get_run(run_id)

    def list_runs(self, limit: int = 50) -> list[dict]:
        return self.repo.list_recent_runs(limit)

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    def _compile(self, kb_id: str, run_id: str) -> CompiledKB:
        facts = self.repo.load_facts(kb_id)
        rules = self.repo.load_rules(kb_id)
        priorities = self.repo.load_priorities(kb_id)
        self.logger.log_kernel_trace(
            run_id,
            "loaded",
            {
                "facts": len(facts),
                "rules": len(rules),
                "priority_edges": len(priorities),
            },
        )
        compiled = compile_knowledge_base(facts, rules, priorities, self.limits)
        stats = compiled.stats()
        self.logger.log_kernel_trace(run_id, "compiled", stats)
        return compiled

    def _check_input_size(self, theory_text: str) -> None:
        # 粗略按语句计数, 精确计数由解析器完成, 这里先挡超大文本
        statements = [s for s in theory_text.split(".") if s.strip()]
        if len(statements) > self.limits.max_input_facts + self.limits.max_input_rules:
            raise ResourceLimitError(
                "输入语句数量超过配置上限",
                details={"statements": len(statements)},
            )

    def _record_failure(
        self, run_id: str, endpoint: str, request: dict, exc: ReasonerError
    ) -> None:
        exc.run_id = run_id
        error = exc.to_dict()
        self.repo.finish_run(
            run_id,
            status_summary=f"error:{exc.category.value}",
            result=None,
            error=error,
        )
        self.logger.log_error(run_id, endpoint, request, error)
