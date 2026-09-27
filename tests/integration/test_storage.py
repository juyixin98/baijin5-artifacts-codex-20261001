"""集成测试: SQLite 存储往返与运行记录持久化。"""
from __future__ import annotations

from app.rulelang import parse_theory
from app.storage.database import connect, initialize
from app.storage.repository import KnowledgeRepository
from tests.conftest import SAMPLES


def _repo(db_path):
    conn = connect(db_path)
    initialize(conn)
    return KnowledgeRepository(conn), conn


def test_theory_roundtrip_preserves_kinds_and_priorities(settings):
    repo, conn = _repo(settings.db_path)
    text = (SAMPLES / "birds.rdrl").read_text(encoding="utf-8")
    theory = parse_theory(text)
    repo.create_kb("birds", "Birds")
    repo.replace_theory("birds", theory)

    # 重新用一个新连接读取, 确认真正落盘
    conn.close()
    repo2, conn2 = _repo(settings.db_path)
    loaded = repo2.load_theory("birds")

    assert sorted(f.render() for f in loaded.facts) == sorted(
        f.render() for f in theory.facts
    )
    assert [r.rule_id for r in loaded.strict_rules] == []
    assert [r.rule_id for r in loaded.defeasible_rules] == ["r1", "r2"]
    assert repo2.load_priorities("birds") == [("r2", "r1")]

    # 恢复出的规则可再次编译并得到相同结论
    from app.kernel.facade import compile_knowledge_base, answer_query
    from app.rulelang import parse_query

    compiled = compile_knowledge_base(
        loaded.facts, loaded.all_rules, loaded.priorities, settings.limits
    )
    answer = answer_query(compiled, parse_query("Flies(polly)"), max_chains=64)[0]
    assert answer.status == "rejected"
    conn2.close()


def test_duplicate_facts_are_idempotent(settings):
    repo, conn = _repo(settings.db_path)
    repo.create_kb("dup", "Dup")
    from app.rulelang import parse_fact

    facts = [parse_fact("Bird(t).")]
    assert repo.add_facts("dup", facts) == 1
    assert repo.add_facts("dup", facts) == 0  # 重复不重复计数
    assert len(repo.load_facts("dup")) == 1
    conn.close()


def test_run_record_persists_success_and_error(settings):
    repo, conn = _repo(settings.db_path)
    repo.create_kb("r", "Runs")

    repo.insert_run("run-ok", "reasoner.query", {"query": "Q"}, kb_id="r")
    repo.finish_run(
        "run-ok", status_summary="ok", result={"answer": 42}, error=None
    )
    repo.insert_run("run-bad", "kb.load_theory", {"theory": "x"}, kb_id="r")
    repo.finish_run(
        "run-bad",
        status_summary="error:input_error",
        result=None,
        error={
            "category": "input_error",
            "error": "RuleLanguageError",
            "message": "坏",
            "details": {"line": 1},
        },
    )

    ok = repo.get_run("run-ok")
    assert ok["result"] == {"answer": 42}
    assert ok["error_category"] is None

    bad = repo.get_run("run-bad")
    assert bad["error_category"] == "input_error"
    assert bad["error_details"] == {"line": 1}

    recent = repo.list_recent_runs()
    categories = {row["run_id"]: row["error_category"] for row in recent}
    assert categories["run-bad"] == "input_error"
    conn.close()
