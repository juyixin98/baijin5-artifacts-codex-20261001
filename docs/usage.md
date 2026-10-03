# 使用文档：规则、接口与失败类别

## 固定处理规则

以下规则是实现的**约定行为**，由独立测试断言，不得由演示性硬编码替代：

### 等位解析

- 等位字符串统一大写，必须是 DNA 碱基（A/C/G/T/N）。
- 变异的 ref 与 alt 必须不同，否则 `INVALID_INPUT`。
- 读段观测等位 == ref → 比特 0；== alt → 比特 1；**其他取值（未知等位）不臆造参考等位**：该观测从 MEC 目标中剔除，计入 `unknown_allele_observations` 并在 `uncertainties` 中说明。
- 同一读段对同一位点的重复观测视为冲突，报 `INVALID_INPUT`，不做任意合并。

### 读段质量 → 代价（固定规则）

- 修正一条观测的代价 **== 其 Phred 质量值**。
- 缺失质量使用 `default_quality`（默认 10），并计入 `defaulted_quality_observations`。
- 质量被钳制到 `[0, max_quality]`（默认 60）。

### 目标函数（加权 MEC）

对块内候选单倍型对 `(h1, h2)`（二等位位点下 `h2 = 1 - h1`）：

```
MEC(h1) = Σ_片段 min( d(f, h1), d(f, h2) )
d(f, h) = Σ_位点 [观测与 h 不一致] × 观测代价
```

片段跨位点的支持冲突以该最小错误修正目标解决。枚举所有 `2^(n-1)` 个规范候选（`h1[0] = 0`，因为相位整体翻转 `(h1,h2)` 与 `(1-h1,1-h2)` 视为等价），取 MEC 最小者；**所有并列最优都报告**，`num_optimal_solutions > 1` 时块标记 `ambiguous`，运行状态为 `AMBIGUOUS`。

### 连通块

- 读段同时覆盖两个位点（均为已知等位）即在位点间连边；连通分量独立定相。
- 每个块的输出含 `phase_note`：相位仅在块内定义，**不编造跨块相位**。
- 无覆盖位点自成一块，并在 `uncertainties` 中单列。

## 接口

### `POST /v1/phase`

请求体：

```json
{
  "sample": "synth-demo",
  "variants": [{"id": "v1", "chrom": "chrSynth", "pos": 101, "ref": "A", "alt": "G"}],
  "reads": [{"read_id": "r1", "variant_id": "v1", "allele": "A", "quality": 30}]
}
```

响应（HTTP 200；领域失败也以 200 返回并在 `status`/`failure` 中说明，模式校验失败为 422）：

| 字段 | 含义 |
|---|---|
| `request_id` | 请求身份，贯穿日志与溯源记录 |
| `status` | `OK` / `AMBIGUOUS` / `FAILED` |
| `failure` | 失败时 `{category, detail}`，否则 `null` |
| `uncertainties` | 不确定结论单列（多解、归属平局、未知等位、无覆盖位点、默认质量） |
| `versions` | 应用版本、算法版本（`mec-exact-1`）、生效配置 |
| `input_sha256` | 规范化请求体的 SHA-256 |
| `summary` | 位点/片段/块计数、未知等位计数、总 MEC |
| `blocks[]` | 每块：`haplotypes`（H1/H2 等位序列）、`mec_score`、`num_optimal_solutions`、`alternative_solutions`、`evidence`（逐位点 ref/alt 支持权重、修正权重与计数、归属平局数）、`phase_note` |

### `GET /v1/runs/{request_id}`

返回该次运行的完整溯源记录（创建时间、版本、配置快照、输入哈希、状态、失败类别、完整结果）。未知 ID 返回 404 与 `RUN_NOT_FOUND`。

### `GET /v1/health` / `GET /v1/version`

健康检查与版本信息。

## 失败类别

| 类别 | 触发条件 |
|---|---|
| `INVALID_INPUT` | ref==alt、非 DNA 等位、重复位点 ID、读段引用未知位点、同读段同位点重复观测 |
| `NO_OBSERVATIONS` | 没有任何可用读段观测（全缺失或全为未知等位） |
| `BLOCK_TOO_LARGE` | 连通块位点数超过 `max_enum_sites`（默认 16） |
| `RUN_NOT_FOUND` | 溯源查询的 request_id 不存在（HTTP 404） |

## 日志

JSON 行日志，每行携带 `request_id`、流水线步骤（`received` → `parsed` → `phased`/`failed` → `recorded`）、应用与算法版本，以及该步骤的关键数值（片段数、块数、总 MEC、不确定结论）。示例：

```json
{"ts": "...", "level": "INFO", "msg": "phasing completed", "request_id": "req_af75ab793cf3",
 "step": "phased", "detail": {"status": "AMBIGUOUS", "num_blocks": 1, "total_mec_score": 30.0,
 "uncertainties": ["..."]}, "app_version": "0.1.0", "algorithm_version": "mec-exact-1"}
```

## 配置（`config/default.yaml`）

| 键 | 默认 | 含义 |
|---|---|---|
| `max_enum_sites` | 16 | 精确枚举的每块位点上限（2^(n-1) 候选） |
| `default_quality` | 10 | 缺失质量的默认代价 |
| `max_quality` | 60 | 质量钳制上限 |
| `max_reported_solutions` | 8 | 每块报告的备选最优解上限 |
| `db_path` | `./data/provenance.db` | SQLite 溯源库路径（`HAPLO_DB_PATH` 可覆盖） |

## 验证方式

- `tests/test_mec.py`：手工计算的 MEC 参考答案（干净、错误读段、对称多解、三位点、翻转等价、质量权重改变胜者）。
- `tests/test_reference_enumeration.py`：对 n=2/3/4 枚举全部 `2^(n-1)` 条真实单倍型对，用**测试内独立的读段生成器**合成证据，断言恢复结果与真值一致（参考答案非被测实现生成）。
- `tests/test_pipeline.py` / `tests/test_api.py`：夹具端到端、失败类别、溯源回读、跨块相位不编造。
