# graphcut-segmentation

合成图像的二标签能量最小化服务：以 s-t 最小割精确求解

```
E(x) = Σ_p D_p(x_p) + Σ_(p,q) V_pq(x_p, x_q),   x_p ∈ {0, 1}
```

技术栈：Python 3.11+ / FastAPI / NumPy / SciPy / Pillow。所有输入均为本地
合成夹具（`data/`），无外部账号与真实业务数据。

## 设计要点

- **能量构图对应**：数据项与平滑项均要求非负；每个 Pairwise 项按
  Kolmogorov–Zabih 方式归约为一条容量 `w = v01+v10-v00-v11` 的边加一元
  修正与常数项，保证 `energy(x) = constant + cut_capacity(x)` 精确成立
  （属性测试 `TestEnergyIdentityProperty` 对随机规格与随机标签逐点核验）。
- **非次模拒绝**：仅当 `v00+v11 <= v01+v10` 时接受；违反时以
  `non_submodular_pairwise` 拒绝，**绝不取绝对值或截断**（图构建层还有
  第二道防线：负边权直接报 `ComputationError`）。
- **硬种子充分约束**：种子权 `M = Σ max(D) + Σ max(V) + 1`，严格大于任意
  标签配置的能量上界；并要求增广容量总量 < 2^52，超出以
  `seed_weight_overflow`（资源耗尽）拒绝，float64 不会溢出丢精度。
- **能量分解与割证书**：每次求解返回 `{data, smoothness, total}` 分解与
  证书：`flow == cut_capacity`、`energy == constant + flow`、种子全部满足、
  以及 SciPy 独立实现（整数缩放容量）的交叉核验流值。任一不一致即
  `certificate_mismatch`（计算失败）。
- **数值内核**：Dinic 最大流为自实现（残留图可直接用于割提取）；SciPy
  `maximum_flow` 仅作独立交叉核验，不作为被测核心。
- **分块作业**：图构建按行带分块流式累积，块边界记录进度并响应取消；
  作业在后台线程执行，状态机
  `pending → running → succeeded / failed / cancelled`。

## 目录结构

```
graphcut/
  errors.py         错误分类（input / state_conflict / resource_exhausted / computation）
  config.py         启动配置（GRAPHCUT_* 环境变量覆盖）
  logging_utils.py  run_id 结构化日志
  models.py         数据契约：ImageContract / EnergySpec / CutCertificate / SolveResult
  specs.py          请求 → EnergySpec（image_model / explicit 数据项；potts /
                    contrast / explicit 平滑项；4-邻接物化）
  energy.py         契约校验（非负、次模）+ 独立能量求值（参考实现）
  seeds.py          硬种子 big-M、冲突检测、溢出防护
  graph.py          能量 → s-t 网络归约（分块构建）
  solver.py         Dinic 最大流 + 割提取
  verify.py         割证书、独立割容量、SciPy 交叉核验
  pipeline.py       同步求解主管线
  jobs.py           分块后台作业管理
  api.py            FastAPI 接口层
scripts/make_sample_data.py   生成 data/ 下的合成夹具（确定性种子）
data/               sample_circle.png / sample_seeds.json / sample_spec.json / 真值掩膜
tests/unit/         能量校验、种子、图构建、求解器、枚举对照
tests/integration/  HTTP 接口、作业生命周期、日志、样例数据端到端
```

## 快速开始

```bash
pip install -r requirements.txt
python scripts/make_sample_data.py        # 重新生成夹具（可选，已提交）
python -m uvicorn graphcut.api:app --port 8978
```

请求示例：

```bash
curl -s -X POST http://127.0.0.1:8978/v1/segment \
  -H 'Content-Type: application/json' -d @data/sample_spec.json
```

实测输出（64×64 合成图，节选）：

```
energy:      {'data': 1.2868, 'smoothness': 0.3957, 'total': 1.6824}
certificate: {'flow_value': 1.6011, 'cut_capacity': 1.6011,
              'graph_constant': 0.0813, 'seeds_satisfied': True,
              'scipy_flow_value': 1.601094, 'consistent': True}
stats:       {'graph_nodes': 4098, 'graph_edges': 12160, 'maxflow_phases': 1}
```

相对无噪声真值圆盘的分割 IoU > 0.9（集成测试断言）。

## 接口

| 方法/路径 | 说明 |
|---|---|
| `GET /health` | 存活检查 |
| `GET /v1/fixtures` | 列出本地样例文件 |
| `POST /v1/segment` | 同步求解，返回标签 + 能量分解 + 割证书 |
| `POST /v1/jobs` | 提交分块后台作业（202，返回 job_id） |
| `GET /v1/jobs/{id}` | 查询作业状态/结果 |
| `POST /v1/jobs/{id}/cancel` | 协作式取消（已完成则 409） |
| `POST /v1/validate` | 独立能量求值：对给定标签重算能量，可比对 `claimed_energy` |

请求体（`image_id` 引用 `data/` 下的 PNG，或完全显式给出数组）：

```json
{
  "image_id": "sample_circle.png",
  "width": 64, "height": 64,
  "unary":    {"mode": "image_model", "fg_mean": 0.8, "bg_mean": 0.2, "sigma": 0.08},
  "pairwise": {"mode": "contrast", "weight": 2.0, "beta": 30.0},
  "seeds":    {"foreground": [2080], "background": [2]}
}
```

`unary.mode`: `image_model`（高斯负对数似然，逐像素平移保证非负）或
`explicit`（`cost0`/`cost1` 数组）。`pairwise.mode`: `potts` / `contrast` /
`explicit`（逐边 `v00,v01,v10,v11`，唯一可能触发非次模拒绝的入口）。

## 错误分类

所有错误响应形如 `{"run_id": ..., "error": {category, code, message, details}}`：

| category | HTTP | 示例 code |
|---|---|---|
| `input` | 400/404 | `non_submodular_pairwise`、`negative_data_term`、`bad_unary_shape`、`unknown_job` |
| `state_conflict` | 409 | `conflicting_seeds`、`job_already_finished` |
| `resource_exhausted` | 413 | `too_many_pixels`、`too_many_edges`、`seed_weight_overflow` |
| `computation` | 500 | `certificate_mismatch`、`maxflow_not_converged`、`unexpected_error` |

## 测试

```bash
python -m pytest tests/ -q
# 58 passed in ~1.3s
python -m pytest tests/ -q --cov=graphcut --cov-report=term-missing
# TOTAL 93%，各模块 ≥ 80%
```

复核要点对应的测试：

- **小图枚举对照**：`tests/unit/test_enumeration.py` 对 2×2/3×2/3×3/4×2
  枚举全部 2^n 标签（用独立的 `energy.evaluate_energy` 打分，不经过图与
  求解器），断言求解能量 == 枚举最优（|Δ| < 1e-9），含带种子约束枚举。
- **冲突种子**：`test_seeds.py` 与 `test_api.py` 断言 409 /
  `conflicting_seeds` 及冲突像素列表。
- **边界邻接**：`test_graph_builder.py` 断言 2×2 恰好 4 条边、角点度 2、
  无跨行回绕。
- **零平滑**：`test_solver.py::TestZeroSmoothing` 与枚举测试断言标签等于
  逐像素 argmin、能量等于 `Σ min(D0, D1)`。
- **独立能量 vs 流割**：`TestHandComputedReduction`（手算 2×1 归约）、
  `TestEnergyIdentityProperty`（随机规格恒等式）、证书断言
  `flow == cut` 且 `energy == constant + flow`，并有 SciPy 独立实现交叉
  核验（`TestAgainstScipy`）。
- **参考答案独立性**：枚举、手算数值、SciPy 均不经过被测核心路径。

## 日志与重放

每次运行分配 `run_id`（作业 id 即 run_id），关键中间状态与判断理由落日志：

```
run_id=b01d3fa9301c event=run_started height=64 pairwise_terms=8064 seeds_fg=4 seeds_bg=4
run_id=b01d3fa9301c event=graph_built constant=0.081332 edges=12160 nodes=4098 seed_weight=128178.33
run_id=b01d3fa9301c event=maxflow_done flow=1.6011 phases=1 source_side=3299
run_id=b01d3fa9301c event=certificate_ok cut=1.6011 energy=1.682432 scipy_flow=1.601094
run_id=b01d3fa9301c event=run_finished data=1.286765 energy=1.682432 smoothness=0.395667
run_id=f6bbeb1fb35f event=request_rejected category='input' code='non_submodular_pairwise' reason='...'
```

按 `run_id` 过滤日志即可重放任意一次运行的输入规模、图统计、流值、
能量分解与拒绝理由。

## 配置

环境变量（前缀 `GRAPHCUT_`）：`MAX_PIXELS`（默认 65536）、`MAX_EDGES`、
`CHUNK_ROWS`、`DATA_DIR`、`SCIPY_CROSS_CHECK`（0 关闭）、`LOG_LEVEL`。
