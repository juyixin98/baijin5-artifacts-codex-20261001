# Paired Randomization Inference Service

有限样本**配对随机实验**的随机化检验（randomization / permutation test）与
**常数处理效应区间反演**（constant-effect confidence-set inversion）的多模块后端。
纯本地合成数据，无外部账号依赖。

技术栈：Python 3.12 · FastAPI · NumPy · SciPy · SQLite · pytest。

---

## 1. 三条行为契约

1. **随机化集合严格按配对设计。** 对 `n` 个配对，每个配对立地交换两成员的处理/对照
   身份，随机化集合恰有 `2**n` 个分配；**绝不跨配对洗牌**（不是 `(2n)!` 的全样本
   排列）。见 `app/stats/design.py`。
2. **反演复用同一个双侧定义，且不强行压成一个区间。** 检验与反演都在同一
   `method` 下进行；接受集表示为**组件并集**（`components`），不连通时分别返回，
   只额外提供一个明确标注为 `hull` 的凸包作为元数据。
3. **组合超预算时报告近似方法与误差。** 当 `2**n` 超过精确枚举预算：
   - 绝对统计量定义 `two_sided_abs`：切换到 Monte Carlo，结果标记 `kind=approximate`，
     并给出标准误、95% 误差界与说明；反演边界误差经**局部斜率传播**后在效应尺度上报告。
   - 概率排序定义 `two_sided_prob`：其双侧 p 值依赖每个统计量取值的**零假设原子概率**，
     朴素 Monte Carlo 会因等质量原子的频数噪声而系统性偏误。此时服务**显式拒绝**
     （失败类别 `approximation_unresolved`）并建议改用 `two_sided_abs`，而不是给出
     会误导的“近似答案”。

### 两种双侧定义

设配平差为 `d_i = treated_i - control_i`，在锐常数效应零假设 `H_tau`（每对效应恰为
`tau`）下，调整差为 `d_i - tau`，统计量

```
S_z(tau) = sum_i z_i (d_i - tau),   z_i ∈ {-1,+1};   S_obs(tau) = sum_i d_i - n*tau.
```

- `two_sided_abs`：`p(tau) = P( |S_z(tau)| >= |S_obs(tau)| )`。其接受集恒为连通区间。
- `two_sided_prob`：一个统计量取值“与观测值一样或更极端”当且仅当它的零假设概率质量
  不超过观测值取值的质量。这是合法的双侧定义，接受集可能**真的不连通**。

---

## 2. 工程组织（多模块，各司其职）

```
app/
  config.py              # 环境变量配置（预算、MC 抽样、网格分辨率），不可变
  stats/
    contracts.py         # 统计契约：PairDesign / 两种双侧定义 / 组件并集 / 失败枚举
    design.py            # 契约1：严格配对随机化集合（2**n，逐对翻转）
    estimator.py         # 估计内核：精确枚举、Monte Carlo、断点反演、网格反演
  evidence.py            # 证据与诊断：请求身份、关键步骤、失败与不确定性分列
  storage.py             # SQLite 持久化（请求、结果、证据）
  service.py             # 编排：校验 → 策略选择 → 内核 → 证据
  api.py                 # FastAPI（create_app 工厂，薄路由）
scripts/
  experiment.py          # 复现实验：枚举所有翻转 + 近似重放
tests/
  reference_oracle.py    # 独立参考答案：fractions.Fraction + 位掩码，另一条实现路径
  test_design.py         # 契约1 的具体数值断言
  test_estimator.py      # 零效应 p 值、反演集合、不连通案例、与独立 oracle 全量比对
  test_approximation.py  # 超预算、误差报告、确定性重放、PROB 显式拒绝
  test_api.py            # 端到端 HTTP、失败分类、请求身份↔日志↔SQLite 关联
  test_diagnostics.py    # 配置、存储、其余失败分支
```

被测核心**不**生成自己的参考答案：`reference_oracle.py` 用精确有理数与独立的位掩码
循环实现，且从不导入应用包；测试同时包含**手算硬编码期望值**与对该 oracle 的全量交叉
验证。

---

## 3. 配置与依赖版本

依赖已按开发验证版本锁定（见 `requirements.txt`）：

```
numpy==2.4.6   scipy==1.15.3   fastapi==0.141.1   pydantic==2.13.5
uvicorn==0.54.0   httpx==0.28.1   pytest==9.1.1
```

全部通过环境变量覆盖（默认值即下列数值，样例见 `.env.example`）：

| 变量 | 默认 | 含义 |
|---|---|---|
| `PAIRTEST_DB_PATH` | `./data/service.db` | 本地 SQLite 文件 |
| `PAIRTEST_EXACT_BUDGET` | `65536` | `2**n` 超过它则改用近似 |
| `PAIRTEST_CROSSING_BUDGET` | `2000000` | PROB 精确反演允许的最大成对交叉数 |
| `PAIRTEST_MC_DRAWS` | `10000` | Monte Carlo 翻转抽样数 |
| `PAIRTEST_MC_SEED` | `20260927` | 复现用基础种子 |
| `PAIRTEST_INVERSION_GRID` | `4096` | 近似反演网格点数 |
| `PAIRTEST_INVERSION_REFINE` | `40` | 每条边界的二分细化次数 |
| `PAIRTEST_LOG_LEVEL` | `INFO` | 日志级别 |

---

## 4. 从干净目录复现

```bash
# 1) （可选）虚拟环境并安装锁定依赖
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# 2) 运行全部独立测试（含覆盖率）
python3 -m pytest tests/ -q --cov=app --cov-report=term-missing

# 3) 复现实验（枚举所有翻转 + 近似重放），写 data/experiment_report.json
PYTHONPATH=. python3 scripts/experiment.py

# 4) 启动服务
PYTHONPATH=. python3 -m uvicorn app.api:app --host 127.0.0.1 --port 8077
```

### 请求样例

```bash
# 精确 p 值（5 对，差 [1,2,3,1,3] -> 2/32 = 0.0625）
curl -s -X POST http://127.0.0.1:8077/api/pvalue \
  -H 'content-type: application/json' -d @examples/pvalue_request.json

# 不连通接受集反演：(-inf,-10) ∪ (-10,-7) ∪ (-7,+inf)
curl -s -X POST http://127.0.0.1:8077/api/invert \
  -H 'content-type: application/json' -d @examples/invert_disconnected_request.json

# 严格配对随机化集合预览
curl -s -X POST http://127.0.0.1:8077/api/randomization-set \
  -H 'content-type: application/json' -d '{"treated":[10,20],"control":[1,2]}'

# 用请求 id 回查持久化记录（响应、日志、SQLite 共用同一 request_id）
curl -s http://127.0.0.1:8077/api/requests/<request_id>
```

---

## 5. 验证材料（关键夹具的确定结果）

| 场景 | 数据（配平差 d） | 期望零效应 p | α 下接受集 |
|---|---|---|---|
| 相同结果 | `[0,0,0,0]` | `1`（两种定义） | 整条实数线 |
| 极端差异 | `[10,-10,10,-10]` | `1`（观测统计量为 0） | 整条实数线 |
| 小枚举 | `[1,2,3]` | ABS `2/8=.25`；PROB `6/8=.75` | ABS@.25 → `[1,3]` |
| **不连通** | `[-1,-4,2,-4,-1]` | ABS `5/16`；PROB `7/16` | PROB@.1 → `(-inf,-10) ∪ (-10,-7) ∪ (-7,+inf)` |
| 近似重放 | `n=18/20`（合成） | MC 估计 ± 95% 误差 | 两次运行逐位相同（确定性种子 + 通用随机数） |

不连通案例中，`-10` 与 `-7` 是两个**孤立拒识点**（精确 p=`2/32<0.1`），中间
`(-10,-7)` 是有界组件；区间内部（如 `-8.5`）不拒识。测试对这些点逐一断言。

### 本次执行的如实记录

```
$ python3 -m pytest tests/ -q --cov=app --cov-report=term-missing
60 passed ...
TOTAL  708 stmts, 14 missed, 98% cover

$ PYTHONPATH=. python3 scripts/experiment.py
identical_outcomes   p=1 exact       set=(-inf,+inf)  certified disconnected=False
extreme_differences  p=1 exact       set=(-inf,+inf)  certified disconnected=False
disconnected_acceptance p=0.4375 exact set=(-inf,-10) U (-10,-7) U (-7,+inf) certified disconnected=True
approximate_replay   p=0.776922 +/- 0.008159; two runs identical = True
```

（近似重放的具体 p 取决于 `PAIRTEST_MC_*` 配置；默认配置下两次运行必然相同。）

---

## 6. 可解释性、失败与不确定性

每个响应是一个证据信封：

- `request_id`：贯穿 JSON 响应、stderr 日志（`[req_…] app.service:<函数>: …`）与
  SQLite 行；
- `steps`：按序记录设计校验、策略选择（精确/近似）、计算与认证状态及处理位置；
- `failures`：**失败原因单列**，使用稳定类别码（in-band 返回，不抛裸 500）；
- `uncertainties`：**不确定结论单列**，与失败严格分开（Monte Carlo 误差、未认证反演）。

失败类别：`invalid_pairs`、`too_few_pairs`、`invalid_alpha`、`invalid_tau`、
`invalid_method`、`approximation_unresolved`、`internal_error`。

## 7. API 摘要

| 方法 & 路径 | 输入关键字段 | 说明 |
|---|---|---|
| `POST /api/pvalue` | `treated[]`,`control[]`,`method`,`tau`,`alpha?` | 双侧随机化 p 值 |
| `POST /api/invert` | `treated[]`,`control[]`,`method`,`alpha` | 常数效应接受集（组件并集） |
| `POST /api/randomization-set` | `treated[]`,`control[]`,`preview_limit?` | 配对随机化集合预览 |
| `GET /api/requests/{id}` | — | 按请求 id 回查持久化记录 |
| `GET /api/requests` | — | 最近请求列表 |
| `GET /health` | — | 健康检查与版本 |
