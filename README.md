# Local-Linear RD Backend（合成阈值分配数据）

一个用 **Python + FastAPI + NumPy + SciPy + SQLite** 实现的局部线性断点回归
（Regression Discontinuity, RD）后端。所有数据均来自本地、带已知真值的合成
夹具；不依赖任何生产账号或真实业务数据。

它在阈值（cutoff）**两侧分别拟合加权局部线性回归**，用两个截距之差估计跳跃
（treatment jump），并提供稳健/自助推断、离散与堆积诊断、密度连续性检验、
稀疏边界与可识别范围报告，以及可复现的带宽选择。

---

## 1. 支持范围与关键取舍（Scope & Trade-offs）

### 支持

- **清晰断点（sharp RD）**，处理状态由 `x` 与阈值的位置决定（`x >= c`，
  可用 `treatment_above=false` 反向编码）。模糊 RD（fuzzy RD）不在范围内。
- **局部线性（一次多项式）** 模型；三种紧支撑核：`triangular`（默认，边界
  最优）、`epanechnikov`、`uniform`。
- 阈值两侧**独立拟合**，各自截距与斜率；跳跃 = 两侧截距之差。
- 带宽：`manual`（调用方指定，可用 `bandwidth_multiplier` 做敏感性扫描）或
  确定性的 **Imbens–Kalyanaraman 风格 ROT**（`ik_rot`，公共带宽、两侧分开拟合，
  含 IK 正则项以保证线性零假设下带宽有限）。
- 推断：异方差稳健三明治方差（默认 **HC3**，可选 HC1）、正态 CI 与 z 检验；
  **Rademacher wild bootstrap**（百分位 CI + 限制零假设 studentized p），
  支持按 `cluster_var` 的聚类 wild / CRV bootstrap。
- 诊断：
  - 离散/格点运行变量、阈值处堆积（mass at cutoff）；
  - **McCrary (2008) 密度连续性检验**（对对数细直方图密度在阈值处做两侧局部
    线性拟合）；
  - 每侧窗内点数与**有效样本量**（核权之和）；
  - **可识别范围**（实际窗宽 + 最近邻支撑间隙）；
  - 设计矩阵条件数。
- 每次运行持久化到 SQLite（完整 request/response JSON），可按 `run_id` 审计。

### 明确的关键取舍

- **不做自动高阶偏差校正。** 局部线性估计有 `O(h²)` 平滑偏差；插件式偏差
  校正噪声大、在边界小样本中可能增大 MSE。系统改为：报告带宽与每侧有效 N、
  报告可识别范围、提供带宽敏感性扫描，并在每个估计上附偏差说明。名义稳健
  CI **本身不覆盖**平滑偏差——这一点在响应的 `bias_notes` 中写明。
- **不以窗内原始均值差代替局部估计。** 当条件均值在阈值附近有斜率时，原始
  均值差有偏；测试 `test_local_linear_unbiased_under_slope_unlike_raw_mean_diff`
  显式断言这一区别。
- **不可识别 ≠ 错误。** 支撑不足/设计奇异时返回 `status="unidentified"`
  （HTTP 200，带显式 `error_code`），而不是硬给一个数；非法输入才是
  `status="error"`（HTTP 422）。异常或未知状态永远不会被统一包装成成功。
- 阈值处的点按 `x >= c` 归入处理侧；其数量单独由 mass-at-cutoff 诊断报告。
- IK ROT 用 Silverman 风格二次 pilot 而非 IK 完整初值迭代，通用核常数
  `C_K=3.4375` 未按每种核重新推导；正式带宽敏感性分析请用 `manual` +
  `bandwidth_multiplier`。

---

## 2. 工程结构

按「统计契约 / 估计内核 / 证据与诊断 / 复现实验」分层，测试与配置独立：

```
app/
  contract.py      # pydantic 契约：枚举、请求/响应、状态与错误类别
  config.py        # 环境变量配置，启动期校验，版本信息
  logging_setup.py # 带 run_id / input_label / 版本 / 步骤的结构化 JSON 日志
  kernels.py       # 核函数（纯函数、确定性、紧支撑）
  bandwidths.py    # manual + IK 风格 ROT
  estimator.py     # 两侧独立加权局部线性拟合（SVD，条件数/杠杆率）
  inference.py     # HC1/HC3 稳健方差、正态 CI、wild/CRV bootstrap
  diagnostics.py   # 离散/堆积、McCrary、稀疏、有效样本、可识别范围
  datasets.py      # 带已知真值的合成 DGP（独立 oracle）
  reference.py     # 独立参考实现（不复用被测核心）
  errors.py        # 类型化错误类别
  pipeline.py      # 编排：契约→带宽→拟合→推断→诊断，显式状态机
  store.py         # SQLite 持久化
  dependencies.py  # 依赖注入
  api.py           # FastAPI（create_app，薄路由，线程池执行同步 CPU 任务）
experiments/
  replication.py   # 蒙特卡洛覆盖率/功效、带宽扫描、参考对比、诊断命中率
tests/             # 单元 + 集成 + HTTP + 独立参考交叉验证（70 个）
results/           # 复现报告与测试日志（运行证据）
```

所有源文件均在 800 行软上限内，函数聚焦、错误显式处理、不可变配置对象。

---

## 3. 本地启动

需要 Python 3.12（NumPy / SciPy / FastAPI / uvicorn / httpx / pytest）。

```bash
# 1) （可选）虚拟环境
python3 -m venv .venv && . .venv/bin/activate

# 2) 安装依赖（精确版本见 requirements.lock）
python3 -m pip install -r requirements.txt        # 或 -r requirements.lock

# 3) 启动服务（默认 http://127.0.0.1:8000）
uvicorn app.api:app --host 127.0.0.1 --port 8000
```

配置（均有默认值，可被环境变量覆盖）：`RD_DB_PATH`（默认 `data/rd_runs.db`）、
`RD_MIN_OBS`（默认 10）、`RD_BOOTSTRAP_REPS`（默认 999）、
`RD_DEFAULT_ALPHA`（默认 0.05）、`RD_LOG_LEVEL`（默认 INFO）。

交互式文档：启动后访问 `http://127.0.0.1:8000/docs`。

---

## 4. 示例请求

先从带真值的夹具取数（也可直接 POST 自己的数据）：

```bash
# 取一个真跳跃=3 的合成样本
curl -s -X POST "http://127.0.0.1:8000/api/v1/fixtures/sharp_jump/sample?n=2000&seed=7"
```

分析（手工带宽、HC3、wild bootstrap）：

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/rd/analyze \
  -H "Content-Type: application/json" \
  -d @examples/request_sharp_jump.json | python3 -m json.tool
```

其中 `examples/request_sharp_jump.json` 是完整请求体。字段要点：

```jsonc
{
  "input_label": "demo-sharp-jump",     // 关联日志/存储
  "data": [{"x": -0.41, "y": 0.2}, ...], // >=6 个点，x/y 必须有限
  "cutoff": 0.0,
  "kernel": "triangular",               // triangular|epanechnikov|uniform
  "bandwidth_method": "manual",         // manual|ik_rot
  "bandwidth": 0.25,                    // manual 必填且 >0
  "bandwidth_multiplier": 1.0,          // 带宽敏感性扫描
  "inference": "hc3",                   // hc3|hc1|none
  "alpha": 0.05,
  "bootstrap_reps": 999,
  "bootstrap_seed": 1234,               // 置种子 => 可复现
  "treatment_above": true
}
```

只读/审计端点：

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/version
curl -s http://127.0.0.1:8000/api/v1/runs?limit=20
curl -s http://127.0.0.1:8000/api/v1/runs/<run_id>
```

也可不用 HTTP，直接调用（见 `examples/run_local.py`）：

```bash
python3 examples/run_local.py
```

---

## 5. 运行测试与复现实验

```bash
# 单元 + 集成 + HTTP + 独立参考交叉验证（70 个）
python3 -m pytest                      # 详细进度写入 results/test_run.log

# 蒙特卡洛覆盖率/功效、带宽扫描、参考对比、诊断命中率
python3 -m experiments.replication --reps 200 --out results
# -> results/latest.md（人类可读）+ results/replication_<时间戳>.json
```

测试日志每行是带 `run_id`、`input_label`、`test_node`、依赖版本、步骤与判定
依据的 JSON，可把一次断言关联回具体输入/运行身份。

### 已实际运行的结论（results/latest.md，reps=200，n=1000/场景）

| 场景 | 偏差 | RMSE | 均值解析SE | SE/MC-SD | 95% 覆盖 | 5% 拒绝率 |
|---|---:|---:|---:|---:|---:|---:|
| 已知跳跃 τ=3 | +0.013 | 0.172 | 0.168 | 0.973 | **0.950** | **1.000**（功效） |
| 无跳跃 τ=0 | +0.013 | 0.172 | 0.168 | 0.973 | **0.950** | **0.050**（显著性水平） |

- 带宽扫描（n=4000）：h=0.20 时 τ̂=3.008；h 增大到 0.40 时 τ̂=3.073，
  定量展示 `O(h²)` 平滑偏差随窗宽增长——印证不自动做偏差校正、改做敏感性
  分析的取舍。
- **独立参考一致性**：核心 vs 独立的显式加权正规方程路径 `|diff|≈2.7e-15`；
  vs `scipy.optimize` BFGS 数值最小化路径 `|diff|≈1.6e-8`；独立 bootstrap-t
  零分布 KS 正态性 p=0.16。
- 诊断命中率：密度不连续标记率 **0.94**；稀疏边界判为不可识别率 **1.00**；
  McCrary 估计的 log 密度跳跃 ≈ `log(2.5)=0.916`（大样本 0.90–0.94）。

参考答案/真值**不是**由被测核心自身生成：真值来自独立 DGP，交叉验证来自
与核心不共享代码的三条数值路径。

---

## 6. HTTP 状态语义

| 情况 | HTTP | `status` | `error_code` |
|---|---:|---|---|
| 估计成功 | 200 | `ok` | – |
| 设计不可识别（支撑不足/奇异/带宽失败） | 200 | `unidentified` | `insufficient_data` / `singular_fit` / `bandwidth_failed` |
| 非法输入或内部数值失败 | 422 | `error` | `invalid_input` / `internal_error` 等 |
| 结构校验失败（pydantic） | 422 | – | FastAPI 校验明细 |
| 未知 `run_id` | 404 | – | – |
| 触发本地限流 | 429 | – | 每客户端 30 次/10 秒 |

---

## 7. 复现性

- 所有随机源（DGP、wild bootstrap）接受显式种子；核与带宽为确定性纯函数。
- 每次响应与日志都带依赖版本（app/python/numpy/scipy/fastapi）。
- SQLite 保存完整请求与响应 JSON。

## 8. 已知限制 / 未执行项（如实记录）

- **statsmodels 交叉验证：未执行。** 本环境系统 Python 受 PEP 668 保护，创建
  虚拟环境/安装 statsmodels 被运行环境的权限分类器拒绝，因此未引入
  statsmodels 作为第二方库参考。替代方案是 `app/reference.py` 中三条不依赖
  核心代码的独立数值路径 + 已知真值 DGP，已满足「参考独立」的要求。在允许
  联网装包的环境中可 `pip install statsmodels` 后追加其 WLS 对照（接口已预留）。
- 仅支持 sharp RD、局部线性、紧支撑核；不含模糊 RD、多阈值、高阶局部多项式
  的完整自动化（契约中多项式度固定为 1）。
- IK ROT 是规则拇指（pilot 简化版），非完整 IK/CCT 最优带宽；正式推断请结合
  手工带宽敏感性分析。
- 限流为单进程内存实现，仅用于本地，不替代网关限流。
