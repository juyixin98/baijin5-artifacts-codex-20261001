"""验收复现脚本：从干净目录重建全部数值证据与服务冒烟结果。

用法：
    python scripts/run_acceptance.py

产物：
    reports/applicability.md  精化 vs 直接低精度对照表、奇异/未达标案例、API 冒烟

退出码：全部检查通过为 0，否则为 1。
"""
from __future__ import annotations

import logging
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

logging.disable(logging.CRITICAL)  # 验收报告只保留结构化结果，日志见测试输出

import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from config.settings import SolverSettings  # noqa: E402
from irsolver.refinement import CONVERGED, NOT_CONVERGED, SINGULAR, solve_system  # noqa: E402
from irsolver.service import create_app  # noqa: E402
from tests.fixtures import (  # noqa: E402
    direct_solve,
    forward_rel_error,
    make_hilbert_system,
    make_random_system,
    make_singular_system,
)

REPORT = ROOT / "reports" / "applicability.md"
SWEEP_KAPPAS = [1e2, 1e6, 1e10, 1e14, 1e16]


def _sweep(settings: SolverSettings) -> tuple[list[str], list[str]]:
    lines = [
        "# 精化 vs 直接低精度：适用范围对照证据",
        "",
        f"- 收敛容差 η ≤ {settings.tolerance:.1e}（分量向后误差）",
        "- 前向误差为相对夹具精确解的 ‖·‖∞ 相对误差",
        "- 直接 fp32/fp64 为 SciPy LU 无精化对照组",
        "",
        "| κ(目标) | κ(估计) | 状态 | 精度级 | 迭代数 | η | 精化误差 | 直接fp32误差 | 直接fp64误差 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    failures: list[str] = []
    for i, kappa in enumerate(SWEEP_KAPPAS):
        fx = make_random_system(n=8, kappa=kappa, seed=1000 + i, name=f"k{kappa:.0e}")
        report = solve_system(fx.A, fx.B, settings)
        col = report.columns[0]
        err_ref = forward_rel_error(col.solution, fx.x_true[0]) if col.solution else np.inf
        err32 = forward_rel_error(direct_solve(fx.A, fx.B, 0, "float32"), fx.x_true[0])
        err64 = forward_rel_error(direct_solve(fx.A, fx.B, 0, "float64"), fx.x_true[0])
        lines.append(
            f"| {kappa:.0e} | {report.condition.kappa:.2e} | {col.status} | {col.tier_used} "
            f"| {col.iterations} | {col.eta_componentwise:.2e} | {err_ref:.2e} "
            f"| {err32:.2e} | {err64:.2e} |"
        )
        if col.status != CONVERGED:
            failures.append(f"κ={kappa:.0e} 未收敛")
        elif err_ref > err32:
            failures.append(f"κ={kappa:.0e} 精化误差 {err_ref:.2e} 劣于直接 fp32 {err32:.2e}")
    return lines, failures


def _edge_cases(settings: SolverSettings) -> tuple[list[str], list[str]]:
    lines = ["", "## 边界案例", ""]
    failures: list[str] = []

    fx = make_hilbert_system(12)
    rep = solve_system(fx.A, fx.B, settings)
    col = rep.columns[0]
    lines.append(
        f"- Hilbert-12（κ 估计 {rep.condition.kappa:.2e}）：状态 {col.status}，"
        f"前向误差界 κ·η = {col.forward_error_bound:.2e} > 1（如实暴露可达精度限制）"
    )
    if not (col.status == CONVERGED and col.forward_error_bound > 1.0):
        failures.append("Hilbert-12 未按预期收敛或前向误差界未暴露")

    fx = make_singular_system(n=8, seed=5)
    rep = solve_system(fx.A, fx.B, settings)
    lines.append(
        f"- 精确奇异矩阵（秩 {rep.condition.rank}/8，{rep.condition.method}）："
        f"状态 {rep.status}，未返回解"
    )
    if rep.status != SINGULAR:
        failures.append("奇异矩阵未被识别")

    tight = replace(settings, tolerance=1e-35, mp_dps=30)
    fx = make_random_system(8, 1e2, 11, "well")
    rep = solve_system(fx.A, fx.B, tight)
    col = rep.columns[0]
    lines.append(
        f"- 容差 1e-35 低于所有精度级下限：状态 {col.status}（已升至 {col.tier_used}），"
        "不误报收敛"
    )
    if col.status != NOT_CONVERGED:
        failures.append("容差不可达时未返回 not_converged")
    return lines, failures


def _api_smoke(settings: SolverSettings) -> tuple[list[str], list[str]]:
    lines = ["", "## 服务冒烟（TestClient 内存调用）", ""]
    failures: list[str] = []
    client = TestClient(create_app(settings))

    ok = client.get("/health").status_code == 200
    lines.append(f"- GET /health -> 200: {'通过' if ok else '失败'}")
    if not ok:
        failures.append("/health 失败")

    fx = make_random_system(8, 1e2, 11, "well")
    payload = {"a": [list(r) for r in fx.A.text], "b": [list(r) for r in fx.B.text]}
    resp = client.post("/solve", json=payload)
    body = resp.json()
    ok = resp.status_code == 200 and body["status"] == "converged" and body["request_id"]
    lines.append(
        f"- POST /solve（良态）-> {resp.status_code}，状态 {body.get('status')}，"
        f"request_id={body.get('request_id')}"
    )
    if not ok:
        failures.append("/solve 良态冒烟失败")

    resp = client.post("/solve", json={"a": [["1", "2"], ["3"]], "b": [["1"], ["2"]]})
    ok = resp.status_code == 422 and resp.json()["error"]["reason"] == "ragged"
    lines.append(f"- POST /solve（残缺矩阵）-> 422 ragged: {'通过' if ok else '失败'}")
    if not ok:
        failures.append("/solve 输入校验失败")

    resp = client.post("/solve", json={**payload, "sensitive": True})
    body = resp.json()
    ok = "fingerprint" in body["matrix_summary"] and body["condition"] is None
    lines.append(f"- 敏感模式脱敏（指纹代替数值画像）: {'通过' if ok else '失败'}")
    if not ok:
        failures.append("敏感模式脱敏失败")
    return lines, failures


def main() -> int:
    settings = SolverSettings()
    sections: list[str] = []
    failures: list[str] = []
    for build in (_sweep, _edge_cases, _api_smoke):
        lines, errs = build(settings)
        sections.extend(lines)
        failures.extend(errs)
    sections += ["", "## 结论", ""]
    sections.append("全部检查通过。" if not failures else "失败项：")
    sections += [f"- {e}" for e in failures]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(sections) + "\n", encoding="utf-8")
    print(f"报告已写入 {REPORT}")
    if failures:
        print(f"共 {len(failures)} 项失败：")
        for e in failures:
            print(f"  - {e}")
        return 1
    print("全部验收检查通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
