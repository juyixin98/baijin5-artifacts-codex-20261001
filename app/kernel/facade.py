"""内核门面: 编译知识库、执行查询、序列化结果。

对外只暴露纯函数/纯数据, 不依赖 SQLite 与 FastAPI, 便于独立单元测试。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..rulelang.ast_nodes import Literal, Rule
from .engine import ReasonerEngine
from .explanation import GoalAnswer, build_goal_answer
from .grounder import ground_program
from .priority import PriorityRelation


@dataclass(frozen=True)
class CompiledKB:
    engine: ReasonerEngine
    fact_count: int
    rule_count: int
    ground_instance_count: int
    reachable_atom_count: int

    def stats(self) -> dict:
        return {
            "facts": self.fact_count,
            "rules": self.rule_count,
            "ground_instances": self.ground_instance_count,
            "reachable_atoms": self.reachable_atom_count,
            "arguments": len(self.engine.store.arguments),
            "attack_edges": len(self.engine.records),
            "grounded_iterations": self.engine.labeling.iterations,
            "accepted_arguments": len(self.engine.labeling.accepted),
            "rejected_arguments": len(self.engine.labeling.rejected),
            "undecided_arguments": len(self.engine.labeling.undecided),
        }


def compile_knowledge_base(
    facts: list[Literal],
    rules: list[Rule],
    priorities: list[tuple[str, str]],
    limits,
) -> CompiledKB:
    """接地 + 一致性检查 + 构造论证 + grounded 求值。"""
    program = ground_program(
        facts, rules, max_instances=limits.max_ground_instances
    )
    priority = PriorityRelation(priorities)
    engine = ReasonerEngine(
        program,
        priority,
        max_arguments=limits.max_arguments,
        fixpoint_iterations=limits.fixpoint_iterations,
    )
    return CompiledKB(
        engine=engine,
        fact_count=len(facts),
        rule_count=len(rules),
        ground_instance_count=len(program.instances),
        reachable_atom_count=len(program.reachable),
    )


def answer_template_matches(
    engine: ReasonerEngine, query: Literal
) -> list[tuple[Literal, dict[str, object]]]:
    """在可达接地原子中枚举与查询模板匹配的实例 (按置换排序)。

    接地查询直接返回自身。变量查询按谓词/元数/强否定符号过滤,
    常量参数要求相等, 同名变量要求取值一致。
    """
    if query.is_ground():
        return [(query, {})]

    wanted_vars = sorted(query.variables())
    results: list[tuple[Literal, dict[str, object]]] = []
    seen_subs: set[tuple] = set()
    for atom in engine.program.reachable:
        if atom.predicate != query.predicate or atom.arity != query.arity:
            continue
        if atom.negated != query.negated:
            continue
        subst: dict[str, object] = {}
        ok = True
        for qterm, aterm in zip(query.args, atom.args):
            kind, value = qterm
            if kind == "c":
                if value != aterm[1]:
                    ok = False
                    break
            else:
                if value in subst and subst[value] != aterm[1]:
                    ok = False
                    break
                subst[value] = aterm[1]
        if not ok:
            continue
        key = tuple((name, subst.get(name)) for name in wanted_vars)
        if key in seen_subs:
            continue
        seen_subs.add(key)
        results.append((atom, {name: subst.get(name) for name in wanted_vars}))

    results.sort(key=lambda pair: pair[0].render())
    return results


def answer_query(
    compiled: CompiledKB, query: Literal, *, max_chains: int
) -> list[GoalAnswer]:
    engine = compiled.engine
    matches = answer_template_matches(engine, query)
    return [
        build_goal_answer(
            engine, atom, substitution, max_chains=max_chains
        )
        for atom, substitution in matches
    ]


def answer_to_dict(answer: GoalAnswer) -> dict:
    def chain_view(view) -> dict:
        return {
            "chain_id": view.chain_id,
            "conclusion": view.conclusion,
            "rule_id": view.rule_id,
            "rule_kind": view.rule_kind,
            "assumptions": view.assumptions,
            "counter_chains": view.counter_chains,
            "tree": view.tree,
        }

    return {
        "goal": answer.goal,
        "substitution": answer.substitution,
        "status": answer.status,
        "reason_code": answer.reason_code,
        "reason": answer.reason,
        "supporting_chains": [chain_view(c) for c in answer.supporting],
        "defeated_chains": [chain_view(c) for c in answer.defeated],
        "pending_chains": [chain_view(c) for c in answer.pending],
        "opposing_evidence": answer.opposing_evidence,
        "chain_counts": {
            "supporting": len(answer.supporting),
            "defeated": len(answer.defeated),
            "pending": len(answer.pending),
        },
    }
