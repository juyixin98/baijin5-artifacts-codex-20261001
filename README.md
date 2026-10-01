# 局部线性断点回归（Local-linear RD）后端

一个从零搭建、可复现的**合成阈值分配数据**的 Sharp Regression Discontinuity
后端。技术栈：Python · FastAPI · NumPy · SciPy · SQLite。全部数据来自本地
确定性合成 DGP，无任何生产账号或真实业务数据。

它实现的统计契约是：在阈值两侧**分别**做核加权局部线性回归，用两个边界
截距之差估计跳跃，**而不是**用两侧原始均值之差；核函数与带宽选择全部具名、
可复现；对离散运行变量、阈值堆积、密度不连续和稀疏边界单独诊断；失败与
"不可识别"是显式状态，绝不统一返回成功。

---

## 1. 目录结构

```
app/
  config.py              本地配置（.env / 环境变量，零配置可启动）
  logging_setup.py       人类可读 stderr + 结构化 JSONL 日志（带 run_id）
  core/
    kernels.py           triangular / epanechnikov / uniform / tricube
    wls.py               加权最小二乘引擎（const / HC1 / HC2 标准误）
    bandwidth.py         ROT 与 IK 风格 plugin 带宽（可复现、会记录回退）
    estimator.py         两侧分别局部线性拟合、跳跃、CI、可识别性判定
    diagnostics.py       离散性 / 阈值堆积 / McCrary 密度检验
    contracts.py         类型化结果、RunStatus、FailureCategory
  dgp/                   五类合成数据夹具（均带种子）
  storage/               SQLite 运行持久化（请求与结果原文落库）
  api/                   FastAPI 路由与 Pydantic 模式
scripts/run_experiments.py  复现实验（Monte Carlo，多带宽，独立参考对比）
tests/                   pytest；reference.py 是完全独立的参考实现
examples/                示例请求
reports/                 实验产物（JSON + 文本报告）
```

设计遵循"多个小文件、高内聚低耦合"，核心统计逻辑、诊断、证据、复现实验、
独立测试各自分层；**不是**单文件、调用壳或固定返回值。

---

## 2. 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt      # 已锁定，见 requirements.txt
./run.sh                                        # 或 uvicorn app.api.main:app
# 服务在 http://127.0.0.1:8000，交互式文档 /docs
```

运行测试：

```bash
.venv/bin/python -m pytest                      # 快速套件（76 项）
.venv/bin/python -m pytest -m slow              # 含 McCrary 水平/功效 Monte Carlo
.venv/bin/python -m pytest                      # 默认 deselect slow，全量见 -m ""
```

复现实验：

```bash
.venv/bin/python -m scripts.run_experiments --quick       # 20 次，秒级
.venv/bin/python -m scripts.run_experiments --reps 200    # 正式报告 -> reports/
```

示例请求：

```bash
bash examples/request.sh
# 或
curl -s -X POST http://127.0.0.1:8000/api/v1/rd/estimate \
  -H 'Content-Type: application/json' \
  -d @examples/request_jump.json
```

---

## 3. API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/health` | 版本与存活 |
| POST | `/api/v1/rd/estimate` | 执行一次 RD 估计 |
| GET  | `/api/v1/runs/{run_id}` | 从 SQLite 取某次运行的完整结果 |
| GET  | `/api/v1/runs?limit=` | 列出历史运行 |

请求字段：`x`、`y`（等长、有限），`cutoff`（默认 0），`kernel`
（`triangular`/`epanechnikov`/`uniform`/`tricube`），`bandwidth`
（`"rot"` / `"ik"` / 正数），`se_type`（`const`/`hc1`/`hc2`，默认 `hc2`），
`alpha`，可选 `run_id`。

返回中：

- `estimate`：`tau`（跳跃）、`se`、`ci95`、`z`、`pvalue`；
- `left` / `right`：两侧各自的边界截距、斜率、带宽、有效样本数（`n` 与
  核权和 `sum_weights`）、三种标准误；
- `status`：`success` / `warning` / `failed`；
- `failure_category` + `failure_reason`：失败时的**具体类别与原因**；
- `diagnostics`：离散性、堆积、McCrary、偏差界、有效样本、**可识别范围**、
  带宽调整/回退记录；
- `versions`：app / python / numpy / scipy 版本。

统计上无法估计（如硬空隙）返回 **HTTP 422 + 结构化失败体**，而不是 500 或
"成功但为空"；失败运行同样落库以便审计。

---

## 4. 统计契约与关键取舍

### 4.1 估计量
对每一侧 `s ∈ {-,+}`，在窗口 `|x-c| ≤ h_s` 内最小化

```
min_{a,b}  Σ_i K((x_i-c)/h_s) · (y_i − a − b(x_i−c))²
```

边界条件期望的估计是截距 `μ̂_s(c) = â`，跳跃 `τ̂ = μ̂_+(c) − μ̂_−(c)`。
两侧**独立拟合、各有带宽**。这从定义上排除了"两侧原始均值差"——在有斜率、
两侧支撑不对称时原始均值差严重有偏（测试 `test_it_is_not_a_raw_mean_difference`
用非对称设计量化了这一点）。

### 4.2 核函数与带宽（可复现）
- 四个紧支撑核具名可选，默认 `triangular`（局部线性在边界上 MSE 最优）。
- `rot`：每侧 `0.9·min(sd, IQR/1.34)·n^{-1/5}`，确定性规则。
- `ik`：Imbens–Kalyanaraman(2012) 风格的渐近 plugin——局部二次 pilot 估
  二阶导与方差、边界 KDE 估密度，代入 plugin 公式；当曲率/密度趋零导致
  MSE 公式无定义时，**显式回退到 ROT 并在 `diagnostics.bandwidth_fallback`
  记录原因**。这是自包含实现，与 R `rdrobust` 数值近似但不逐位相同
  （正则化步骤不同），README 在此明确说明，不宣称逐位一致。
- 固定正数带宽也可直接传入。

### 4.3 置信区间与标准误
- 默认报告 **HC2 杠杆修正的异方差稳健三明治标准误**，另提供 HC1 与同方差
  公式，三者在每侧结果中都给出，便于比较。
- 两侧独立，跳跃方差为两侧边界截距方差之和：`se(τ̂)=√(se_+²+se_−²)`，
  CI 用正态临界值 `z_{1−α/2}`。
- **平滑偏差没有被假装为零**：局部线性截距偏差为 `O(h²)`。我们用核的
  二阶矩给出偏差界 `|bias| ≤ B·h²·μ₂(K)/2`，其中曲率 `B` 用局部斜率/带宽
  保守代理，结果写入 `diagnostics.bias.bias_bound_abs`。使用者可据此做
  bias-aware 判断；本项目默认给传统（undersmoothed）CI，并把偏差界作为
  证据暴露，而不是默默忽略或用未经说明的"自动修正"。

### 4.4 离散运行变量与阈值堆积（单独诊断）
- `discreteness`：不同取值占比、最大单点质量、阈值点占比；判为粗/离散时
  建议聚类稳健或离散方法。
- `heaping`：阈值点质量相对两侧最近质量点的 Poisson-rate 检验。
- **恰好在阈值上的观测处理归属模糊，两侧拟合一律排除**（`n_at_cutoff_excluded`）。
- 离散网格下选定带宽可能只覆盖一个格点 → 设计秩亏。估计器会在每侧把带宽
  自适应加宽到覆盖 ≥10 个**不同**支撑点且设计条件数健康，并把每次加宽记入
  `bandwidth_adjustments`；按"不同点"而非"观测数"计数，否则重复格点仍会秩亏。

### 4.5 密度不连续（McCrary）
`mccrary` 采用 McCrary (2008) 的网格对齐分箱 + **核加权局部线性 Poisson
似然（IRLS）** 分别拟合两侧边界对数密度，统计量
`θ = log f̂_+(c) − log f̂_−(c)`，标准误来自每侧 Fisher 信息。用 Poisson
似然而非"对 log 计数做高斯 WLS"，是因为后者低估计数的泊松不确定性、
经验水平偏大；Poisson 版本还能自然纳入空箱。Monte Carlo（200 次级别的
专门测试）显示零假设下水平保守、密度 3 倍跳跃下 θ 无偏（≈ log 3）且高功效。

### 4.6 稀疏边界与可识别性（关键取舍）
当阈值附近存在**硬性空区**时，无论怎么加宽，窗口里都没有近阈值数据，边界
截距只能靠把局部直线外推过空区得到。系统定义

```
外推因子 = 到最近观测的空隙 / 窗口内实际数据跨度
```

- 因子 ≥ 1：到达阈值所需外推距离 ≥ 整个局部数据跨度 →
  `status=failed`、`failure_category=non_identifiable`，不给虚假精确的数；
- 因子在 [0.25, 1)：仍给估计，但 `warning` 明确提示跨空隙外推；
- 同时报告 `identifiable_range`（每侧携带正核权的 x 范围）与有效样本数。

加宽不能凭空制造空区里的数据，这是有意的"宁可显式失败"的取舍。

---

## 5. 验证方案与结果

四类必备情景 + 多带宽 + 独立参考，全部在 `tests/` 与 `scripts/` 中，
**断言具体数值与失败类别**，不是"接口能调通"。

独立参考实现 `tests/reference.py` **不导入任何被测代码**：系数用手写正规
方程 `numpy.linalg.solve`、`numpy.polyfit`、`scipy.stats.linregress` 三条
独立路径交叉验证；标准误用独立的 HC0/HC1 三明治。被测核心与参考答案因此
不是同一实现的自证。

`scripts/run_experiments.py --reps 200` 的代表性结果（见
`reports/rd_experiments.txt`）：

| 实验 | 结果 |
|------|------|
| 已知跳跃 τ=10 | 偏差 ≈ 0.001–0.014，RMSE 0.16–0.29，95% 覆盖 0.93–0.94 |
| 无跳跃 | 偏差 ≈ 0，检验 5% 水平 0.02–0.07 |
| 密度不连续（3:1） | 水平估计无偏（偏差 ≈ −0.02），McCrary 检出率 0.98，θ̄≈1.075（真 log3=1.099） |
| 稀疏硬空隙 | h≤0.4 全部 `non_identifiable`；h=0.8（窗口跨度超过空隙）给带外推警告的估计 |
| 与独立参考系数差 | 最大 ≈ 1e-14（机器精度） |

有效样本数与可识别范围在每次估计和每行实验结果中都报告。

---

## 6. 日志与可追溯性

- 每次请求/完成都以 `run_id` 关联，含 `step: received → completed`、版本、
  带宽、τ、状态与判定依据。
- 失败在 `ERROR` 级别记录且带 `failure_category`；测试断言失败运行**绝不会
  以 success 出现**（`tests/test_logging.py`）。
- JSONL 日志路径由 `RD_LOG_FILE` 控制；测试会话开始会记录一次版本横幅。

---

## 7. 支持范围与局限（明确的边界）

- **仅 Sharp RD**（处理状态在阈值处确定性跳变）。不实现 fuzzy RD、多维分配
  变量、时空 RDD，也不做聚类相关标准误（离散场景仅给出方法学告警）。
- 大样本正态近似 CI，不提供有限样本 t 临界或随机化推断。
- IK 为论文公式的自包含复现，非 `rdrobust` 封装；不承诺逐位一致。
- 偏差界使用局部曲率的保守代理，是透明的数量级证据，而非严格最坏情形常数。
- 数据全为本地合成夹具；SQLite 仅用于本地审计持久化，不含并发/鉴权设计。

## 8. 配置

复制 `.env.example` 为 `.env` 或导出环境变量：`RD_DB_PATH`、`RD_LOG_LEVEL`、
`RD_LOG_FILE`、`RD_HOST`、`RD_PORT`。默认零配置即可本地启动。
