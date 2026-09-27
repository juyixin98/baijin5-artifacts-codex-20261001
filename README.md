# irsolver：低精度分解 + 高精度残差的迭代精化线性求解器

求解稠密线性系统 `A·X = B`。核心思想：**用低精度 LU 分解获得廉价的校正方程
求解器，用高精度残差（始终基于原矩阵）驱动迭代精化**，在接近低精度分解成本
的情况下达到高精度解的向后误差水平；校正无改善时沿精度阶梯升级，仍不达标
则如实返回未达标，绝不误报收敛。

## 算法与精度阶梯

每个右端列独立执行：

1. 在当前精度级分解 `A` 并直接求解初始 `x`；
2. 用**原矩阵**按该级残差精度计算 `r = b − A·x` 与分量向后误差 `η`；
3. `η ≤ tolerance` 则接受；否则解校正方程 `A·dx = r`（复用同一分解），`x ← x + dx`；
4. `η` 停滞（连续 2 次改善不足 `stall_ratio`）、发散、出现非有限值或迭代耗尽时，
   升级到下一精度级；
5. 升至最高级仍不达标 → `not_converged`；高精度秩判定确认奇异 → `singular`。

| 阶段 | 分解精度 | 残差精度 | 机器 epsilon | 默认迭代预算 |
|---|---|---|---|---|
| fp32 | float32（SciPy LU） | float64（原矩阵） | 6.0e-08 | 12 |
| fp64 | float64（SciPy LU） | longdouble 80 位（原矩阵） | 2.2e-16 | 8 |
| mpmath | 任意精度 LU（本仓库实现，部分主元） | 同精度（原矩阵十进制原文） | 1e-60（可配） | 6 |

**残差不变量**：残差永远由原始输入矩阵计算 —— fp32/fp64 级用原矩阵的
float64/longdouble 映像，mpmath 级用输入的十进制原文，绝不使用低精度分解中
被舍入的矩阵（`tests/test_residual_evidence.py` 对此有专门断言）。

## 误差判据与状态语义

- 接受判据：分量相对向后误差 `η(x) = max_i |b−A·x|_i / (|A||x|+|b|)_i ≤ tolerance`，
  同时报告范数向后误差与前向误差界 `κ·η`。
- 条件数：float64 SVD 快速估计；`κ ≥ 1e12` 或不可分辨时升级 mpmath 高精度 SVD，
  并在同一高精度下做数值秩判定（阈值 `σmax·n·10^-(dps-10)`）。
- 可达精度限制：前向相对误差不可能优于 `κ·ε`（工作精度机器 epsilon），响应中的
  `accuracy_note` 会如实给出该限制；`κ·η ≥ 1` 时解的每一位都可能不可信。
- 列状态：`converged` / `not_converged` / `singular`；整体状态：
  `converged` / `partial` / `failed` / `singular`。
- 防误报：测试中所有 `converged` 判定都由独立参考模块（Householder QR，
  与核心 LU 路径零共享）重算 `η` 复核。

## 目录结构

```
config/settings.py     配置（默认值 + IRSOLVER_ 环境变量覆盖 + 校验）
src/irsolver/
  inputs.py            数值输入：解析、校验、规范化（保留十进制原文）
  precision.py         精度阶梯定义
  factor.py            分解内核（SciPy LU / mpmath LU）
  residual.py          残差计算（原矩阵，按阶段精度）
  evidence.py          误差证据（η、前向误差界、迭代轨迹）
  condition.py         条件数估计与数值秩判定
  refinement.py        迭代精化主流程（升级策略，逐列独立）
  diagnostics.py       请求标识、决策日志、脱敏摘要
  service.py           FastAPI 接口
  reference.py         独立参考实现（仅测试/验收对照）
tests/                 独立组织的测试（夹具 fixtures.py 与 5 个测试模块）
scripts/run_acceptance.py  验收复现脚本
samples/solve_request.json 请求样例
reports/applicability.md   生成的对照证据（可由脚本重新生成）
```

## 依赖版本

Python 3.12；运行时：`numpy==2.4.6`、`scipy==1.15.3`、`mpmath==1.3.0`、
`fastapi==0.141.1`、`uvicorn==0.54.0`、`pydantic==2.13.5`；
测试：`pytest==9.1.1`、`pytest-cov==7.1.0`、`httpx==0.28.1`。

## 从干净目录复现

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# 1. 全部测试（51 个，含具体数值断言与失败类别断言）
python -m pytest

# 2. 覆盖率（要求 ≥80%，实测 94%）
python -m pytest --cov --cov-report=term-missing

# 3. 验收证据（重新生成 reports/applicability.md）
python scripts/run_acceptance.py

# 4. 启动服务
python -m uvicorn irsolver.service:app --app-dir src --port 8000

# 5. 请求样例（精确解为 [1, 2, 3]）
curl -X POST http://127.0.0.1:8000/solve \
  -H 'Content-Type: application/json' \
  -d @samples/solve_request.json
```

## API

### `POST /solve`

请求体：

```json
{
  "a": [["4", "1", "0.5"], ["1", "3", "0.25"], ["0.5", "0.25", "2"]],
  "b": [["7.5"], ["7.75"], ["7"]],
  "tolerance": 1e-10,
  "sensitive": false,
  "include_solution_text": true
}
```

- `a`：n×n 系数矩阵；`b`：n×m 右端（**每列独立求解、独立报告**）。
  元素接受 JSON 数值或十进制字符串（字符串可保留超出 float64 的精度）。
- `tolerance`：可选，覆盖默认收敛容差（0, 1) 内）。
- `sensitive`：敏感模式，响应与日志只给形状 + SHA-256 指纹，不给数值画像。
- `include_solution_text`：同时返回 `repr` 级精度的解文本（容差 < 1e-16 时
  float64 解文本本身已成为瓶颈，需以此为准）。

响应（200）：`request_id`、`status`、`condition`（κ、估计方法、秩判定）、
`accuracy_note`、`matrix_summary`、`columns[]`（每列状态、精度级、迭代轨迹、
η、前向误差界、解、判定理由 message）、`solution`、`diagnostics.journal`
（接受/拒绝/升级/判奇异的完整决策记录）。非有限浮点值序列化为 `null`。

输入非法（非方阵、残缺行、NaN/Inf、维度不符、超 512 阶）→ 422，带机器可读
`error.reason`（`not_square` / `ragged` / `non_finite` / `dimension_mismatch` /
`invalid_shape` / `invalid_entry` / `too_large`）。

### `GET /health` → `{"status": "ok", "version": "0.1.0"}`

## 配置（环境变量）

| 变量 | 默认 | 含义 |
|---|---|---|
| `IRSOLVER_TOLERANCE` | 1e-10 | 分量向后误差收敛阈值 |
| `IRSOLVER_STALL_RATIO` | 0.5 | 停滞判定比例 |
| `IRSOLVER_FP32_MAX_ITER` / `IRSOLVER_FP64_MAX_ITER` / `IRSOLVER_MP_MAX_ITER` | 12 / 8 / 6 | 各级迭代预算 |
| `IRSOLVER_MP_DPS` | 60 | mpmath 级十进制工作精度 |
| `IRSOLVER_COND_THRESHOLD` | 1e12 | 升级高精度 SVD 的条件数阈值 |
| `IRSOLVER_REFERENCE_DPS` | 120 | 独立参考解精度（仅测试/验收） |

## 诊断与脱敏

每次求解分配 `request_id`，贯穿结构化日志与响应。决策日志记录每次迭代的
η、每次升级的原因（停滞/耗尽/非有限值/零主元）、接受或判未达标的依据。
敏感模式（`sensitive: true`）下，日志不输出 κ、η、范数等数值画像，矩阵摘要
以形状 + 指纹代替。

## 已知限制

- 稠密中小规模系统（≤512 阶）；mpmath 级为任意精度纯 Python 运算，大矩阵较慢。
- longdouble 残差精度依赖 x86 80 位扩展（本验收环境满足）；其他平台 fp64 级
  残差下限可能升高，mpmath 级不受影响。
- 解的 JSON 输出为 float64；容差 < 1e-16 时请用 `include_solution_text`。
