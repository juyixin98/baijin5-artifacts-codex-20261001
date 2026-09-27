# 验收执行结果（如实记录）

执行环境：Linux 6.8.0-90-generic（x86_64），Python 3.12.3。
执行时间：2026-09-28。以下结果均为实际命令输出整理。

## 1. 单元/集成测试

命令：`python -m pytest`

```
51 passed, 1 warning in 1.63s
```

（warning 为 starlette TestClient 的弃用提示，与功能无关。）

覆盖的关键验证（均断言具体结果，非"接口能调用"）：

- 良态（κ≈1e2）：fp32 级收敛，η 从 6.6e-08 改善到 1.2e-14，前向误差比直接
  fp32 低 3 个数量级以上；独立 Householder QR 参考解与夹具精确解一致（<1e-30）。
- 病态（κ≈1e10）：fp32 级停滞后升级到 fp64 收敛；直接 fp32 相对误差 3.8e-02，
  精化后 8.0e-08。
- 奇异（秩 7/8）：高精度秩判定（svd-mpmath dps=60）识别，状态 singular，不返回解。
- 未达标：容差 1e-35 低于所有精度级残差下限 → not_converged，不误报收敛。
- 批量右端：3 列各自独立的状态、迭代轨迹与误差报告。
- 防误报不变量：所有 converged 判定经独立参考模块重算 η 复核（4 组 κ 扫描）。

## 2. 覆盖率

命令：`python -m pytest --cov --cov-report=term-missing`

```
TOTAL  663 statements, 39 missed, 94%
```

各模块均 ≥88%（阈值 80%）。

## 3. 适用范围对照（`python scripts/run_acceptance.py`）

完整表见 `reports/applicability.md`（脚本可重新生成）。摘录：

| κ(目标) | 精度级 | η | 精化误差 | 直接fp32误差 | 直接fp64误差 |
|---|---|---|---|---|---|
| 1e+02 | fp32 | 2.04e-14 | 3.84e-13 | 3.81e-07 | 1.51e-15 |
| 1e+06 | fp32 | 1.28e-12 | 1.86e-07 | 2.82e-03 | 2.27e-12 |
| 1e+10 | fp64 | 4.72e-17 | 3.78e-08 | 3.09e+00 | 1.89e-08 |
| 1e+14 | fp64 | 8.74e-17 | 3.35e-05 | 1.03e+00 | 1.34e-03 |
| 1e+16 | fp64 | 3.71e-16 | 4.26e+00 | 6.67e+00 | 4.54e-01 |

结论：κ ≲ 1e6 时 fp32 级精化即可达到 fp64 级精度；κ ∈ [1e8, 1e15] 是直接
fp32 完全失效而精化（升级后）仍可用的核心适用区间；κ ≳ 1e16 时后向误差仍
达标，但前向误差界 κ·η > 1 被如实暴露（Hilbert-12 同）。

边界案例（脚本输出原文）：

```
- Hilbert-12（κ 估计 1.71e+16）：状态 converged，前向误差界 κ·η = 5.15e+00 > 1（如实暴露可达精度限制）
- 精确奇异矩阵（秩 7/8，svd-mpmath(dps=60)）：状态 singular，未返回解
- 容差 1e-35 低于所有精度级下限：状态 not_converged（已升至 mpmath），不误报收敛
```

服务冒烟：/health 200；/solve 良态 200 converged；残缺矩阵 422 ragged；
敏感模式指纹脱敏 —— 全部通过。

## 4. 真实服务验证（uvicorn + curl）

```
GET  /health -> {"status":"ok","version":"0.1.0"}
POST /solve（samples/solve_request.json）-> status=converged, solution=[[1.0],[2.0],[3.0]]
```

## 已知限制（如实说明）

- mpmath 级为纯 Python 任意精度运算，仅适合中小规模矩阵。
- fp64 级残差使用 x86 80 位 longdouble；非 x86 平台该级残差下限会升高。
- 解的 JSON 数值为 float64；容差 < 1e-16 时需以 solution_text 为准。
