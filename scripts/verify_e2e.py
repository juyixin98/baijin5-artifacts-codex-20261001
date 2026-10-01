#!/usr/bin/env python3
"""独立端到端校验：用 Python 自己解析类型化 CSV、计算多重集参考结果，
与 set_ops CLI 在 in_memory / auto(溢写) / external 三种模式下的输出做
逐行多重集比较。不调用任何被测实现的代码。

用法: verify_e2e.py <project_dir> [--rows N]
输出: <project_dir>/docs/results/<run_id>/ 下的结果 JSON、模式比对与总结。
"""
import csv
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path


class Rng:
    """Deterministic xorshift64 (independent of production generator)."""
    def __init__(self, seed):
        self.x = seed | 1

    def next(self):
        x = self.x
        x ^= (x << 13) & 0xFFFFFFFFFFFFFFFF
        x ^= x >> 7
        x ^= (x << 17) & 0xFFFFFFFFFFFFFFFF
        self.x = x & 0xFFFFFFFFFFFFFFFF
        return self.x


LABELS = ["123", "1,23", "12,3", "1", "22", '"q"', "héllo", "", "\\N", "dup000"]


def write_stress_csv(path, n, seed):
    rng = Rng(seed)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["grp:bigint", "label:text", "flag:boolean", "score:double"])
        for _ in range(n):
            grp = 0 if rng.next() % 100 < 30 else rng.next() % 120
            label = LABELS[rng.next() % len(LABELS)]
            flag = "" if rng.next() % 11 == 0 else ("true" if grp % 2 == 0 else "false")
            score = "" if rng.next() % 13 == 0 else f"{grp / 3.0:.2f}"
            w.writerow([grp, label, flag, score])


def parse_typed_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        types = [h.split(":", 1)[1].strip() for h in header]
        ms = Counter()
        for raw in reader:
            if not raw:
                continue
            row = []
            for ty, cell in zip(types, raw):
                if cell == "" or cell == "\\N":
                    row.append(None)
                elif ty == "bigint":
                    row.append(int(cell))
                elif ty == "double":
                    v = float(cell)
                    # 与生产编码一致的规范化: -0 == +0, NaN 单一形态
                    if v == 0.0:
                        v = 0.0
                    row.append(v)
                elif ty == "boolean":
                    row.append(cell.lower() in ("true", "t", "1", "yes", "y"))
                else:
                    row.append(cell)
            ms[tuple(row)] += 1
    return types, ms


def reference(op, qual, l, r):
    is_all = qual.upper() == "ALL"
    keys = set(l) | set(r)
    out = Counter()
    for k in keys:
        a, b = l.get(k, 0), r.get(k, 0)
        if op == "UNION":
            n = (a + b) if is_all else (1 if a or b else 0)
        elif op == "INTERSECT":
            n = min(a, b) if is_all else (1 if a and b else 0)
        else:  # EXCEPT
            n = max(0, a - b) if is_all else (1 if a and not b else 0)
        if n:
            out[k] = n
    return out


def normalize_rows(types, rows):
    ms = Counter()
    for row in rows:
        norm = []
        for ty, v in zip(types, row):
            if v is None:
                norm.append(None)
            elif ty == "bigint":
                norm.append(int(v))
            elif ty == "double":
                f = float(v)
                if f == 0.0:
                    f = 0.0
                if math.isnan(f):
                    norm.append("NaN")
                else:
                    norm.append(f)
            elif ty == "boolean":
                norm.append(bool(v))
            else:
                norm.append(v)
        ms[tuple(norm)] += 1
    return ms


def run_cli(bin_path, left, right, op, qual, mode, mem, spill, run_id):
    mode_flag = {"in_memory": "in-memory", "auto": "auto", "external": "external"}[mode]
    cmd = [
        str(bin_path), "run",
        "--left", str(left), "--right", str(right),
        "--op", op, "--qualifier", qual.lower(), "--mode", mode_flag,
        "--memory-bytes", str(mem), "--fanout", "4", "--max-depth", "6",
        "--run-id", run_id,
    ]
    if spill is not None:
        cmd += ["--spill-dir", str(spill)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def main():
    project = Path(sys.argv[1])
    rows_n = 6000
    if "--rows" in sys.argv:
        rows_n = int(sys.argv[sys.argv.index("--rows") + 1])

    run_id = f"e2e-{int(time.time())}"
    out_dir = project / "docs" / "results" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    gen_dir = out_dir / "fixtures"
    gen_dir.mkdir()

    # Also exercise the CLI fixture generator once (delivery smoke check).
    subprocess.run([
        str(project / "target" / "debug" / "set_ops"), "fixture",
        "--out", str(gen_dir / "cli_smoke"), "--rows", "200",
        "--keyspace", "20", "--kind", "all",
    ], check=True, capture_output=True)

    # Independent Python-generated pair with heavy overlap, duplicates, skew
    # (grp=0 ~30%), nested NULLs and collision-prone strings: both sides draw
    # the SAME row distribution from independent xorshift streams, producing a
    # rich intersection and meaningful multiset arithmetic.
    left = gen_dir / "left.csv"
    right = gen_dir / "right.csv"
    write_stress_csv(left, rows_n, 0xA5A51234)
    write_stress_csv(right, rows_n, 0x9ABCDEF0)

    types, lms = parse_typed_csv(left)
    _, rms = parse_typed_csv(right)

    # 同时用手工 edge 夹具验证 NULL/边界字符串。
    edge_l = project / "fixtures" / "edge_left.csv"
    edge_r = project / "fixtures" / "edge_right.csv"
    _, elms = parse_typed_csv(edge_l)
    _, erms = parse_typed_csv(edge_r)

    ops = [
        ("UNION", "all"), ("UNION", "distinct"),
        ("INTERSECT", "all"), ("INTERSECT", "distinct"),
        ("EXCEPT", "all"), ("EXCEPT", "distinct"),
    ]
    modes = [
        ("in_memory", 64 * 1024 * 1024, None),
        ("auto", 4096, out_dir / "spill-auto"),
        ("external", 64 * 1024 * 1024, out_dir / "spill-ext"),
    ]

    summary = []
    failures = 0
    for dataset, lp, rp, lm, rm in [
        ("generated", left, right, lms, rms),
        ("edge", edge_l, edge_r, elms, erms),
    ]:
        for op, qual in ops:
            want = reference(op, qual, lm, rm)
            for mode, mem, spill in modes:
                if dataset == "edge" and mode == "in_memory":
                    pass  # still run
                rid = f"{run_id}-{dataset}-{op.lower()}-{qual}-{mode}"
                rc, stdout, stderr = run_cli(
                    project / "target" / "debug" / "set_ops",
                    lp, rp, op, qual, mode, mem, spill, rid,
                )
                case = {"dataset": dataset, "op": op, "qualifier": qual, "mode": mode}
                if rc != 0:
                    case["ok"] = False
                    case["error"] = stderr.strip()[:400]
                    failures += 1
                    summary.append(case)
                    continue
                result = json.loads(stdout)
                got = normalize_rows(types if dataset == "generated" else ["text", "text"],
                                     result["rows"])
                # NaN 归一化键
                want_n = Counter({("NaN",): 0})
                want_n.clear()
                for k, v in want.items():
                    nk = tuple("NaN" if isinstance(x, float) and math.isnan(x) else x for x in k)
                    want_n[nk] += v
                ok = got == want_n
                case["ok"] = ok
                case["output_rows"] = result["stats"]["output_rows"]
                case["output_distinct"] = result["stats"]["output_distinct"]
                case["spills"] = result["stats"]["spills"]
                case["spill_bytes"] = result["stats"]["spill_bytes"]
                case["max_resident_bytes"] = result["stats"]["max_resident_bytes"]
                case["recursions"] = result["stats"]["recursions"]
                if not ok:
                    failures += 1
                    extra = set(got) - set(want_n)
                    missing = set(want_n) - set(got)
                    diff = {k: (got.get(k), want_n.get(k)) for k in set(got) & set(want_n)
                            if got[k] != want_n[k]}
                    case["extra_sample"] = [repr(x)[:120] for x in list(extra)[:3]]
                    case["missing_sample"] = [repr(x)[:120] for x in list(missing)[:3]]
                    case["multiplicity_sample"] = [
                        {"key": repr(k)[:120], "got": g, "want": w}
                        for k, (g, w) in list(diff.items())[:3]
                    ]
                if mode != "in_memory" and dataset == "generated":
                    assert result["stats"]["spills"] >= 1, f"{mode} must spill"
                summary.append(case)

    report = {
        "run_id": run_id,
        "generated_rows_each": rows_n,
        "cases": len(summary),
        "failures": failures,
        "results": summary,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))

    print(f"run_id={run_id}")
    print(f"cases={len(summary)} failures={failures}")
    for c in summary:
        flag = "OK " if c["ok"] else "FAIL"
        extra = ""
        if "output_rows" in c:
            extra = (f" rows={c['output_rows']} distinct={c['output_distinct']} "
                     f"spills={c['spills']} spill_bytes={c['spill_bytes']} "
                     f"max_resident={c['max_resident_bytes']} recursions={c['recursions']}")
        print(f"[{flag}] {c['dataset']:9s} {c['op']:9s} {c['qualifier']:8s} "
              f"{c['mode']:9s}{extra}" + ("" if c["ok"] else f" :: {c.get('error', 'mismatch')}"))
    print(f"report: {out_dir / 'report.json'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
