# depthcov — 基因组区间逐段覆盖深度与质量过滤后端

一个可审查、可运行的参考实现：对**合成比对记录**（SAM 风格的 CIGAR）做
逐碱基 / 逐段覆盖深度计算，带质量过滤、配对重叠去重、外部排序、深度直方图、
加权长度守恒校验与 SQLite 结果溯源，并通过 FastAPI 暴露验证接口。

栈：Python 3.10+ · FastAPI · NumPy · SQLite（标准库）· pytest。全部输入为
本地合成夹具，无需任何生产账号或真实数据。

---

## 1. 固定的坐标语义（审查重点）

这些约定在整个代码库中**固定**，不是可选项：

| 约定 | 规则 |
|---|---|
| 坐标基制 | **0-based（零基）** |
| 区间 | **半开区间 `[start, end)`**：单碱基位置 `p` 表示为 `[p, p+1)`，`end` 碱基本身**不**计入 |
| 覆盖操作 | `M` / `=` / `X` 同时消耗 query 与 reference，**计覆盖** |
| 缺口 | `D` / `N` 只推进 reference，**是缺口、绝不计覆盖**，并把覆盖块切开 |
| 插入 | `I` / `S` 只推进 query，不落 reference、不计覆盖 |
| 无坐标 | `H` / `P` 不推进用于覆盖的 reference |
| 边界相接 | `[0,5)` 与 `[5,10)` 相接但不重叠：位置 5 只属于第二条，任何位置深度都不为 2 |

例：`ref_start=1, CIGAR=3M2D2M` 产生两个覆盖块 `[1,4)`、`[6,8)`，
缺口碱基 `[4,6)` 深度为 0；`ref_end=8`（含缺口），但覆盖碱基数为 5。

### 去重策略（显式，杜绝无意双计配对重叠）

去重不是隐式副作用，而是命名策略，见 `src/depthcov/coverage.py`：

- **`union_per_query`（默认）**：同一 `query_name` 的所有覆盖块先做**并集合并**，
  再计入深度。因此一对重叠的 paired-end mates（共享 QNAME）在任何单碱基上
  至多贡献 +1，重叠片不会双计。并集是真正的区间并，部分重叠、包含、多块都正确。
- **`per_record`**：每条被接受的记录独立计数。仅当确认记录来自不同分子时使用；
  此策略下重叠会双计，这是**显式选择**，响应中会回报该策略。

此外，在 `union_per_query` 下，**完全相同的重复记录**（`query_name` + 参考 +
覆盖块三元组完全一致）被明确裁决为 `duplicate` 而拒绝；同 QNAME 但**不同块**
（如配对重叠）则保留并做并集，不会被误拒。被 FLAG 标记的 PCR/optical 重复
（`is_duplicate=true`，对应 SAM FLAG 0x400）默认按 `duplicate` 拒绝，可配置放行。

---

## 2. 模块职责（不是单文件脚本，也不是空接口工程）

```
src/depthcov/
  models.py         领域类型：Alignment/CoveredBlock/DepthSegment/DepthResult/Verdict/原因枚举
  cigar.py          CIGAR 严格解析 + 缺口感知的覆盖块展开（含独立的逐碱基参考实现）
  filtering.py      质量过滤与逐记录裁决（固定的检查顺序，接受/拒绝/无法判定）
  coverage.py       领域算法：区间并集、扫描线分段、NumPy 逐碱基深度、直方图、三路守恒
  external_sort.py  超内存输入：分块落盘 TSV run + heapq k 路归并（有界内存）
  engine.py         编排：流→(外排)→裁决→精确去重→深度→溯源；产出 RunReport 与诊断
  provenance.py     SQLite 溯源：run / 逐记录裁决 / 分段 / 直方图全部可回查复核
  diagnostics.py    诊断：请求/记录标识、关键状态、接受/拒绝理由、敏感字段脱敏
  config.py         配置与环境变量覆盖、严格校验
  synthetic.py      确定性本地合成夹具（含可手算的小参考）
  schemas.py        FastAPI/pydantic 请求响应模型
  api.py            验证接口（/healthz、/analyze、/analyze/tsv、/runs/{id}）
  __main__.py       服务入口 python -m depthcov
tests/
  oracle.py                 独立暴力 oracle（手写 CIGAR 扫描 + set 逐碱基），与核心零共享代码
  test_cigar.py … test_api.py   按主题独立组织，断言具体结果与失败类别
scripts/demo.py     本地端到端演示（强制多段外排）
examples/           合成 references.tsv / alignments.tsv
```

### 加权长度守恒（审查重点）

对每条参考同时用三种独立方式计算“总覆盖量” `weighted_length`，不一致即抛错：

1. 被接受块/并集块的长度直接求和；
2. 扫描线分段上 `Σ (end−start)·depth`；
3. NumPy 逐碱基深度求和；

并用直方图交叉验证 `Σ depth·histogram[depth] == weighted_length`，
且 `Σ histogram.values() == ref_length`（每个参考碱基恰好被归入一个深度桶，
含 0 深度桶）。见 `tests/test_sweep.py`。

---

## 3. 快速开始

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # 或 pip install -e .
pytest -q                               # 运行全部测试
python scripts/demo.py --chunk-size 2   # 本地端到端演示（强制多个外排 run）
```

> 免安装方式（或在 PEP 668 外部管理的系统 Python 上）：无需安装，
> 直接 `PYTHONPATH=src pytest -q`、`PYTHONPATH=src python scripts/demo.py`。

### 启动服务

```bash
pip install -e .                         # 在 venv 内注册 depthcov 入口
depthcov --host 127.0.0.1 --port 8000
# 或免安装：PYTHONPATH=src python -m depthcov --port 8000
```

调用：

```bash
curl -s http://127.0.0.1:8000/healthz

curl -s -X POST http://127.0.0.1:8000/api/v1/analyze \
  -H 'content-type: application/json' \
  -d '{
    "references":[{"name":"c","length":10}],
    "alignments":[
      {"query_name":"a","ref_name":"c","ref_start":1,"cigar":"3M2D2M","mapq":60}
    ]
  }'
# per_base_depth = [0,1,1,1,0,0,1,1,0,0]
# segments = [{1..4 d1},{6..8 d1}], weighted_length = 5（缺口 [4,6) 不计）
```

原始 TSV（坏行不会让请求失败，而会计为 undetermined）：

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/analyze/tsv \
  -H 'content-type: application/json' \
  -d '{"references":[{"name":"chrDemo","length":20}],"tsv":"..."}'
```

### 环境变量配置

`DEPTHCOV_MIN_MAPQ`、`DEPTHCOV_DEDUP_POLICY`（`union_per_query`|`per_record`）、
`DEPTHCOV_REJECT_FLAGGED_DUPES`、`DEPTHCOV_SORT_CHUNK`、`DEPTHCOV_DB`、
`DEPTHCOV_LOG_LEVEL`。非法值在启动时即报错（见 `tests/test_config.py`）。

---

## 4. 错误语义（README 明确约定）

分两层，**记录级问题不是 HTTP 错误**：

### HTTP 层
| 状态 | 触发 |
|---|---|
| `200` | 请求可执行；个别记录的接受/拒绝见响应体 |
| `400` | 请求逻辑不可用，例如参考名重复 |
| `422` | 请求体不满足 schema（字段缺失、坐标为负、MAPQ 越界等），**不执行任何计算** |
| `404` | `/api/v1/runs/{run_id}` 查询不存在的 run |

### 记录级（在响应 `diagnostics[*]` 与 SQLite `records` 表中回报）
固定检查顺序（`filtering.adjudicate`），每条记录给出 `outcome` 与 `reason`：

- `accepted` —— 贡献覆盖，附覆盖块数、缺口后覆盖碱基数、`[start,end)`；
- `rejected`，原因取值：
  - `unknown_reference` —— 参考不在注册表；
  - `duplicate` —— FLAG 0x400 重复，或 union 策略下的完全重复记录；
  - `low_mapq` —— `mapq < min_mapq`（阈值**含等号**：mapq==阈值接受）；
  - `invalid_cigar` —— CIGAR 无法解析（空、零长度、未知操作符、尾部垃圾等）；
  - `out_of_bounds` —— 派生 `ref_end > ref_length`（注意 `end` 为开区间，恰等长度接受）；
  - `no_covered_bases` —— CIGAR 仅含 D/N（如 `4N`），无可计覆盖碱基；
- `undetermined` / `malformed_record` —— 原始 TSV 行无法解析成记录。
  这是**无法判定**而非拒绝：单独计数（`counts.undetermined`），不静默丢弃。

每条诊断都带 `request_id` 与 `record_id`、关键状态（参考、起终点、MAPQ、块数、
覆盖碱基数）以及人类可读的“为什么接受/拒绝/无法判定”。`x-request-id` 头可
贯穿请求并在响应头原样返回。

### 敏感信息
原始行文本、序列、质量值、sample/patient/donor 等键一律经
`diagnostics.redact` 输出为不可还原的指纹（仅保留长度、少量首字符与
SHA-256 前缀），诊断与日志中不回显明文（见 `tests/test_diagnostics.py`）。

---

## 5. 如何复核（测试断言具体结果，而非“接口可调”）

```bash
pytest -q          # 当前：86 passed
```

- `test_cigar.py`：CIGAR 解析的**具体失败类别**（空/零长度/未知 op/尾部垃圾）、
  缺口切块、I/S/H/P 坐标语义；覆盖块展开与逐碱基参考实现逐项一致。
- `test_depth_tiny.py`：10 bp 小参考上断言**手写的逐碱基数组**
  `[0,1,1,2,2,2,2,1,0,0]`、具体接受/拒绝清单与直方图。
- `test_boundaries.py`：半开 `[start,end)`、边界相接、单碱基区间、末端恰好相接。
- `test_dedup.py`：配对重叠不双计（并集加权 9 而非 6+5=11）、重复读段、
  `per_record` 下重叠显式双计（11）、同 QNAME 部分重叠取并集。
- `test_filtering.py`：每种拒绝原因逐条断言、阈值含等号、检查顺序、端到端台账。
- `test_sweep.py`：扫描线分段极大且不重不漏、分段可重建逐碱基数组、
  加权长度三路守恒 + 直方图守恒。
- `test_external_sort.py`：多 run 全局有序、编解码往返、落盘文件清理、坏行类别。
- `test_provenance.py`：仅用 SQLite 中回读的分段/直方图行即可独立重算总覆盖量。
- `test_api.py`：具体深度数组、计数、诊断分类、400/422/404、TSV undetermined。
- `test_oracle_crosscheck.py`：与**独立暴力 oracle**（`tests/oracle.py`，
  手写扫描，与核心零共享代码）在手工用例与 30+ 组随机夹具、两种去重策略、
  多个 MAPQ 阈值下逐碱基一致，且拒绝集合一致。

> 参考答案不依赖被测核心：`tests/oracle.py` 是另一套手写实现，
> `src/depthcov/cigar.py` 的 `naive_covered_positions` 也是刻意最简的独立扫描。

---

## 6. 外部排序（超内存输入）

`external_sort.external_sort` 以有界 `chunk_size` 分块：每块在内存排序后
spill 成带版本头的临时 TSV run，再用 `heapq.merge` 做 k 路归并，drain 时
删除 run 文件。内存占用不随输入规模增长。`scripts/demo.py --chunk-size 2`
强制产生多个 run 以便观察。TSV 编解码字段数/类型/枚举非法时抛
`RecordCodecError`，在 TSV 接口中转为 `undetermined`。

## 7. 结果溯源（SQLite）

每个 run 持久化：run 元数据与计数、每条输入记录的坐标/CIGAR/裁决/原因/
覆盖碱基数、每参考聚合结果、完整直方图、每个恒定深度分段。复核者可
`SELECT` 分段行独立重算 `Σ length·depth` 并与报告值比对（`test_provenance.py`
正是这样做的）。默认 `:memory:`；`DEPTHCOV_DB=/path/to.db` 落盘，可跨进程重开。
