# LORD 3 在线 FDR 控制教学服务

固定假设序列（fixed-sequence）在线多重检验的**可审查实现**，只支持一种冻结的
LORD 类规则：**LORD 3**（Javanmard & Montanari,
*Online Rules for Control of False Discovery Rate …*, arXiv:1603.09000, 2018）。

每次决策的阈值**只依赖过去结果**；已花预算（wealth）与已记录决策不可重写。
规则参数、初始财富与奖励公式全部冻结并带指纹。本项目只使用本地合成数据，
不依赖任何生产账号或真实业务数据。

---

## 1. 冻结的统计契约（唯一服务的规则）

| 项 | 冻结值 / 公式 | 出处 |
|---|---|---|
| 目标水平 α | `0.05` | 参数选择 |
| 初始财富 w₀ | `0.005` | 满足 0 ≤ w₀ ≤ α |
| 奖励 b₀ | `0.045` | 满足 b₀ > 0 且 w₀+b₀ = α |
| γ 序列 | `γ_m = C·log(max(m,2)) / (m·exp(√log(max(m,2))))`，`C=0.07720838` | 论文 Eq. 31 |
| 阈值 | `α_i = γ_{i−τ_i} · W(τ_i)` | Eq. 18 |
| 财富更新 | `W(t)=W(t−1)−α_t + b₀·𝟙{p_t ≤ α_t}` | Eq. 9/15 |
| 判定 | `p_i ≤ α_i` 则拒绝（闭区间） | 与 onlineFDR 一致 |
| 初始锚点 | 首次发现前 τ=0，W(0)=w₀ | Eq. 18 |

γ₁ = γ₂ ≈ 0.0116382058（`max(m,2)` 对 log(1)=0 的保护）；数值核验
`Σ_{m≥1} γ_m ≈ 0.935 ≤ 1`。

契约指纹（任何冻结常量改动都会使它变化，并使历史证据审计失败）：

```
d126245803f7c3885df0c1e5652ad79ccea2983c45d5618a85619225308f3cb1
```

### 奖励时点（已冻结的关键差异）

本实现采用**论文口径**：奖励 b₀ 在发生拒绝的**同一步**进入财富，因此 W(τ_i)
已包含 τ_i 时刻挣到的奖励。注意 R 包 **onlineFDR** 的 C++ `lord.cpp`（version=3）
使用滞后一步的奖励（`R[i-1]*b0`）。两者财富/阈值数值不同，但在 onlineFDR 官方
教学样例 `p=(1e-7, 0.1, 0.00025, 0.07)` 上拒绝模式都是 **R=(1,0,1,0)**。该差异
同时写入 `GET /contract` 与本文档，不隐藏。

### 适用条件与依赖假设

1. 假设按**预先固定的顺序**到达，每个假设只检验一次；
2. 第 i 个 p 值（及产生它的检验）必须在 α_i 披露**之前**确定——严格在线；
3. p 值相互**独立**（或零假设 p 值与非零假设 p 值独立）；LORD 3 **不**依赖 PRDS；
4. p 值合法（零假设下超均匀：P(p≤x) ≤ x），取值 [0,1]；
5. 这是在线程序，**不能用离线 Benjamini–Hochberg 冒充在线决策**；
6. 保证是 FDR = E[V/R]（论文 mFDR 口径）≤ α 的**期望**陈述，
   不是“单次实验错误发现比例必低于 5%”。

---

## 2. 模块关系（四条工程边界）

```
HTTP 边界                统计契约                 估计内核
app/service.py  ──使用──▶ app/contracts.py ◀────── app/lord3.py
  FastAPI 路由            冻结常量/公式/指纹         纯函数序列封装
  分类错误映射            step(): 严格在线一步       run_sequence()
        │                    │  ▲                      │
        ▼                    ▼  │                      ▼
app/errors.py         app/store.py (SQLite)   证据与诊断
分类错误契约            仅追加 + 哈希链            app/diagnostics.py
INPUT / NOT_FOUND /     热状态持久化/重启           replay_verify():
STATE_CONFLICT /        重复 id / 容量限制          独立重放 + 链校验
EVIDENCE_INTEGRITY /                                 discovery_stats(): V/FDP
RESOURCE_EXHAUSTED /          ▲
COMPUTATION_FAILED            │
                        复现实验 experiments/runner.py
                        合成夹具 app/simulation.py
                        固定种子 JSONL 可重放日志
```

- `app/contracts.py`：唯一的规则真相来源，**无 I/O**，不可变状态
  （`step` 返回新状态，绝不就地修改）。
- `app/lord3.py`：同一冻结规则的整序列便捷封装（实验用）；不是 BH，
  阈值仍逐个由过去结果决定。
- `app/store.py`：SQLite **只追加**证据库；无 UPDATE/DELETE 业务路径；
  每行 SHA-256 哈希，链向前一行并绑定契约指纹。
- `app/diagnostics.py`：审计时把库存 p 值**独立重放**一遍（不信任热计数器），
  逐字段比对 + 哈希链校验；并按真值标签统计 V、FDP、power。
- `app/simulation.py`：本地合成 p 值（全零 Uniform；混合流零假设 Uniform、
  非零假设 Beta(a,1)），种子可复现。
- `experiments/runner.py`：固定种子 Monte Carlo，JSONL 日志含
  `run_no / seed / key_states / stats / judgement`，可凭 seed 重放整行。
- `app/service.py`：FastAPI，仅做传输与校验。

模块间错误契约统一在 `app/errors.py`：

| 类别 | 代码 | HTTP | 触发场景 |
|---|---|---|---|
| INPUT | E1001 | 422 | p 值非有限实数或越界（含 bool/字符串） |
| INPUT | E1002 | 422 | hypothesis_id 空白/含空白字符/超长 |
| INPUT | E1003/E1004/E1005 | 422 | 冻结参数不符 / 请求体畸形 / 分页非法 |
| NOT_FOUND | E1020 | 404 | run_id 不存在 |
| STATE_CONFLICT | E1021 | 409 | 重复 hypothesis_id（拒绝重写历史） |
| EVIDENCE_INTEGRITY | E1030 | 500 | 重放或哈希链审计失败 |
| RESOURCE_EXHAUSTED | E1040 | 507 | 达到 run 决策上限 |
| COMPUTATION_FAILED | E1050 | 500 | 阈值/财富更新出现非有限值 |

---

## 3. 本地验证命令

```bash
# 依赖（已在本机按下列版本安装；建议虚拟环境）
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# 1) 全量测试 + 覆盖率（门槛 80%，当前 96%）
python3 -m pytest

# 2) 只跑某类复核
python3 -m pytest tests/test_kernel_trajectory.py   # 手算短序列阈值轨迹
python3 -m pytest tests/test_oracle_crosscheck.py   # 对独立 oracle 的差分测试
python3 -m pytest tests/test_store_persistence.py   # 重启 / 重复 id / 容量
python3 -m pytest tests/test_diagnostics_audit.py   # 事后篡改被审计识别
python3 -m pytest tests/test_experiments.py         # 固定种子批次与重放

# 3) 重新生成（并人工核对）测试里冻结的参考字面量
python3 scripts/print_reference_values.py

# 4) 复现实验（固定种子；artifacts/*.jsonl 可重放）
python3 scripts/run_experiments.py --quick   # 50×200 冒烟
python3 scripts/run_experiments.py           # 500×500

# 5) 启动服务（默认 SQLite 在 data/lord3.db）
uvicorn app.service:app --reload
#   GET /health  GET /contract
#   POST /runs ; POST /runs/{id}/decisions ; POST /runs/{id}/audit
```

### 预期判断方式

- **手算轨迹**：官方样例必须给出阈值
  `(5.8191e-05, 5.8123e-04, 5.8123e-04, 1.0914e-03)` 与拒绝 `(1,0,1,0)`，
  财富逐值等于测试中冻结的字面量。
- **差分测试**：被测内核与独立 oracle（`tests/reference/`，不 import app）
  在数千随机流上逐字段一致；并有断言保证二者无 import 耦合。
- **不可重写**：同一 id 第二次提交返回 409，首行决策与已花财富不变；
  直接改 SQLite 中的 p 值/阈值后，`audit` 必须 `ok=false` 并指出失败字段，
  且 `chain_ok=false`。
- **重启**：关闭后用同一数据库文件新建 store，τ/W/序号恢复，后续阈值
  与连续运行完全一致，重复 id 仍被拒。
- **统计复核**：断言的是**跨重复的经验量**（固定种子字面量 + 区间判断），
  不是单次保证。全零流 FDP 均值远低于 α；混合流经验 FDR 低于 α、power 为正。
  单次复现的 `judgement` 明确写出“FDR 是期望，非逐次上限”。
- **参考答案独立性**：手算字面量来自 stdlib 独立 oracle，
  不由被测核心实现生成；夹具字面量来自指定 NumPy 种子。

---

## 4. 已复现的实验结果（固定种子，本机 Python 3.12.3）

`python3 scripts/run_experiments.py`（500 重复 × 500 检验，base seed 20260929）：

| 流 | 经验 FDR（mean FDP） | 平均拒绝 | 平均错误发现 V | P(R>0) | power |
|---|---|---|---|---|---|
| 全零 Uniform | 0.0020 | — | 0.002 | 0.002 | 0.000 |
| 混合（20% Beta(0.05,1)） | 0.0252 | — | 1.990 | 1.000 | 0.757 |

完整逐条记录见 `artifacts/*.jsonl`（含每 run 的 seed、关键中间状态与判定理由），
汇总见 `artifacts/*.summary.json`。这些是**估计值**：Monte Carlo 误差下即使规则
有效，估计也可能偶然超过 α；结论是“该批次经验 FDR 低于目标 0.05”，不是证明。

---

## 5. HTTP 快速示例

```bash
curl -s localhost:8000/contract | jq .fingerprint
RID=$(curl -s -XPOST localhost:8000/runs -H 'content-type: application/json' \
  -d '{"max_decisions":100}' | jq -r .run_id)
curl -s -XPOST localhost:8000/runs/$RID/decisions \
  -H 'content-type: application/json' -d '{"hypothesis_id":"H1","p_value":0.0000001}'
curl -s -XPOST localhost:8000/runs/$RID/audit | jq .ok   # true
```

---

## 6. 依赖版本（已验证环境）

Python 3.12.3；fastapi 0.141.1、uvicorn 0.54.0、pydantic 2.13.5、
numpy 2.4.6、scipy 1.15.3、starlette 1.7.0、anyio 4.15.1；
测试 httpx 0.28.1、pytest 9.1.1、pytest-cov 7.1.0。完整钉版见
`requirements.txt` / `pyproject.toml`。

## 7. 明确不做的事

- 不提供 LORD 1/2、Alpha-investing、SAFFRON 等其他规则，也不接受运行时改参；
- 不做离线 BH；不根据未来 p 值给阈值；
- 不提供证据行的修改/删除接口；
- 不接入真实数据或外部服务。
