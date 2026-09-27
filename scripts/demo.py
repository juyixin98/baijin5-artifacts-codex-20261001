#!/usr/bin/env python3
"""命令行演示: 装载样例理论并打印可解释结论, 无需启动 HTTP 服务。

用法:
    python3 scripts/demo.py                 # 跑全部内置场景
    python3 scripts/demo.py samples/birds.rdrl "Flies(X)"
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_settings  # noqa: E402
from app.kernel.facade import (  # noqa: E402
    answer_query,
    answer_to_dict,
    compile_knowledge_base,
)
from app.rulelang import parse_query, parse_theory  # noqa: E402

SCENARIOS = [
    ("samples/birds.rdrl", "Flies(X)"),
    ("samples/nixon_diamond.rdrl", "Pacifist(nixon)"),
    ("samples/naf_open_world.rdrl", "Wings(tweety)"),
    ("samples/naf_open_world.rdrl", "Wingless(tweety)"),
    ("samples/mutual_exclusion.rdrl", "Red(x)"),
]


def run_one(theory_path: Path, query_text: str, limits) -> None:
    theory = parse_theory(theory_path.read_text(encoding="utf-8"))
    compiled = compile_knowledge_base(
        theory.facts, theory.all_rules, theory.priorities, limits
    )
    answers = answer_query(compiled, parse_query(query_text), max_chains=64)
    print(f"\n### 理论 {theory_path}  查询 {query_text}")
    print(f"统计: {json.dumps(compiled.stats(), ensure_ascii=False)}")
    for answer in answers:
        d = answer_to_dict(answer)
        print(
            f"  {d['goal']:22} {d['status']:11} {d['reason_code']}"
            f"  置换={d['substitution']}"
        )
        for chain in d["supporting_chains"]:
            print(f"      [支持] {chain['rule_id']}  假设={chain['assumptions']}")
        for chain in d["defeated_chains"]:
            for c in chain["counter_chains"]:
                print(f"      [击败] {c['attack_kind']} <- {c['rule']}: {c['detail']}")
        for chain in d["pending_chains"]:
            for c in chain["counter_chains"]:
                print(f"      [悬置] {c['attack_kind']} <-> {c['rule']}: {c['detail']}")
        for opp in d["opposing_evidence"]:
            print(f"      [反证] {opp['conclusion']}: {opp['detail']}")


def main() -> int:
    settings = load_settings()
    if len(sys.argv) == 3:
        run_one(Path(sys.argv[1]), sys.argv[2], settings.limits)
        return 0
    for theory_rel, query in SCENARIOS:
        run_one(ROOT / theory_rel, query, settings.limits)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
