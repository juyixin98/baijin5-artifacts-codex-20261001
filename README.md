# motifscan — 合成 DNA 的 PWM 模体扫描与显著性校准

一个多模块后端服务：给定合成 DNA 序列（裸序列或 FASTA）和一个计数矩阵形式的
模体，在正反两条链上扫描所有窗口，并相对**显式声明的背景模型**给出**精确**
p 值（短模体全枚举）与多重扫描校正。所有请求（含失败请求）都落入 SQLite
溯源库，可按 `request_id` 回放。

> 定位说明：本服务给出的是声明背景模型下的**统计命中**，不代表任何生物功能
> 结论。输入面向合成序列，不接入外部数据库或真实基因组资源。

## 模块划分

| 模块 | 职责 |
|---|---|
| `app/sequence.py` | 合成序列解析：裸序列 / 多记录 FASTA，IUPAC 模糊碱基归一为 `N`，非字母字符报错 |
| `app/pwm.py` | 计数矩阵 → 概率（背景加权伪计数）→ log2 优势比 PWM；背景模型严格校验 |
| `app/significance.py` | 全枚举 4^k 个词的精确得分分布、尾概率 p 值、阈值反解、Bonferroni / BH 校正 |
| `app/scan.py` | 双链窗口扫描、未知碱基策略、重叠命中保留、坐标一律落在正链 |
| `app/provenance.py` | SQLite 溯源：请求身份、配置快照、版本、请求哈希、结果/失败原因 |
| `app/main.py` | FastAPI 装配：请求身份注入、分步日志、阈值解析、失败持久化 |
| `app/config.py` | 声明式配置（环境变量覆盖），默认值见下 |
| `app/errors.py` | 稳定的机器可读失败类别 |

## 声明的统计模型（关键约定）

- **背景模型**：默认均匀 `A=C=G=T=0.25`，可按请求或环境变量覆盖。所有阈值与
  p 值都相对该背景分布校准。背景概率必须**严格为正**且和为 1（±1e-6）；
  零概率背景会使 log 优势比无定义，因此以 `ZERO_BACKGROUND_PROBABILITY`
  明确拒绝，而不是静默产生 `-inf`。
- **伪计数**：`p'_{i,b} = (n_{i,b} + pc·bg_b) / (N_i + pc)`，默认 `pc = 1.0`。
- **得分**：`w_{i,b} = log2(p'_{i,b} / bg_b)`，窗口得分为各位置之和。
- **精确分布**：枚举全部 4^k 个词，按背景概率加权聚合（等分合并前按 9 位
  小数舍入，属声明的离散化）。`p(t) = P_bg(S ≥ t)`。模体长度上限 `k ≤ 10`
  （约 105 万词），这是"精确枚举"换"长度受限"的刻意取舍。
- **阈值**：`score_threshold` 与 `pvalue_threshold` 二选一；都不给时用默认
  `p = 0.05`。p 值阈值反解为"尾概率 ≤ p 的最小可达得分"；若最优词都达不到，
  记警告 `THRESHOLD_UNREACHABLE` 并返回零命中（不报错）。
- **多重扫描校正**：对一次请求中**全部已评估窗口**（双链，跳过的不计）计算
  p 值，再做 Bonferroni 与 Benjamini–Hochberg 校正，命中携两种校正后取值。
- **坐标**：一律为正链 0 基半开区间 `[start, end)`。负链命中表示该窗口的
  反向互补序列匹配，`matched` 字段保留正链窗口序列身份；重叠命中各自保留。
- **未知碱基**：解析阶段 IUPAC 模糊码归一为 `N`。扫描策略二选一：
  `skip`（默认，窗口不计入评估，单独计数）或 `marginalize`（未知位置贡献
  背景期望 `Σ_b bg_b·w_{i,b}`）。

## 本地启动

```bash
pip install -r requirements.txt          # 版本已锁定
uvicorn app.main:app --port 8000
```

配置（环境变量，前缀 `MOTIFSCAN_`）：`BG_A/BG_C/BG_G/BG_T`、`PSEUDOCOUNT`、
`MAX_MOTIF_LENGTH`、`UNKNOWN_POLICY`、`DEFAULT_PVALUE_THRESHOLD`、
`DB_PATH`（默认 `./motifscan.db`）。

## 接口

- `GET  /v1/health` — 存活与版本
- `GET  /v1/config` — 当前生效配置与算法版本
- `POST /v1/scan` — 扫描（见下）
- `GET  /v1/scan/{request_id}` — 按请求身份回放结果或失败原因

### 示例请求

```bash
curl -s -X POST http://127.0.0.1:8000/v1/scan \
  -H 'Content-Type: application/json' -d @examples/scan_request.json | python3 -m json.tool
```

`examples/scan_request.json`：

```json
{
  "sequences": ">synthetic_1\nACGTACGTACGT\n>synthetic_2\nTTACGGTAACCT\n",
  "motif_counts": [[4,0,0,0],[0,4,0,0]],
  "pvalue_threshold": 0.1,
  "unknown_policy": "skip",
  "label": "example"
}
```

响应包含：`request_id` / `request_hash` / 版本、生效配置快照、阈值解析
（模式、得分阈值、实际达到的 p 值）、汇总（评估/跳过窗口数、警告）、命中
列表（链向、正链坐标、得分、p 值、Bonferroni、BH）。完整演示脚本：
`bash examples/run_example.sh`。

### 失败类别（422，除 404 外）

`INVALID_SEQUENCE`、`INVALID_MOTIF`、`INVALID_PSEUDOCOUNT`、
`INVALID_BACKGROUND`、`ZERO_BACKGROUND_PROBABILITY`、
`BACKGROUND_NOT_NORMALIZED`、`MOTIF_TOO_LONG`、`INVALID_THRESHOLD`、
`UNKNOWN_POLICY_INVALID`、`INVALID_REQUEST`（请求体模式校验失败）；
`SCAN_NOT_FOUND`（404）；未预期异常归一为 `INTERNAL`（500，详情见服务端
日志中对应 `request_id`）。失败请求同样持久化，
`GET /v1/scan/{request_id}` 可查到 `status: "failed"` 与错误类别。

### 日志

每个请求注入 `request_id`，按步骤输出
（`sequences_parsed` → `pwm_built` → `distribution_enumerated` →
`threshold_resolved` → `scan_completed` → `scan_persisted`），失败时记录
类别与原因，便于把接口结果、日志与溯源库三方对照。

## 测试

```bash
python3 -m pytest                 # 49 个测试
python3 -m pytest --cov=app       # 覆盖率（实测 97%）
```

- 参考答案**不**来自被测核心：`tests/reference_motif.py` 提供手算常数
  （k=2 参考模体的三个得分档与 1/16、7/16、1 三档 p 值）和一个独立的纯
  Python 朴素实现（k=3 非均匀背景交叉核验）。
- 覆盖：反向链坐标映射（含与反向互补序列正链扫描的对偶性质）、零背景概率
  拒绝、窗口边界（长度等于/短于模体、末尾窗口）、p 值核验、Bonferroni/BH
  手算值、未知碱基两种策略、全部失败类别、API 端到端与溯源回放。
- 实测记录见 `TEST_REPORT.md`。

## 支持范围与关键取舍

- 仅 ACGT + IUPAC 模糊码；非字母字符直接拒绝（合成输入，不做容错猜测）。
- 精确枚举限制 `k ≤ 10`；更长模体需要近似方法（如动态规划卷积或大偏差
  界），本工程刻意不引入，以保证 p 值"精确且可解释"。
- 得分等值聚合按 9 位小数舍入；阈值比较容差 1e-9。
- 统计命中 ≠ 生物功能；输出仅相对声明的背景模型有效。
- SQLite 单文件溯源，面向本地/单机部署；未做并发写优化与鉴权。
