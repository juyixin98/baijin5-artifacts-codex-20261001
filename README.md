# groupagg — 分组精确百分位 / mode / 有序字符串聚合后端

纯后端服务，技术栈 **Rust + Axum + Arrow2**，无任何外部业务依赖：所有数据来自
本地确定性合成夹具（`src/fixtures.rs`）或请求内联 JSON，落盘只使用本地临时目录。

实现的算子（均为分组精确算子）：

| 算子 | SQL 对应 | 语义 |
|---|---|---|
| `percentile_cont` | `PERCENTILE_CONT(q) WITHIN GROUP (ORDER BY v)` | 连续百分位，线性插值；结果恒为 float64 |
| `percentile_disc` | `PERCENTILE_DISC(q) WITHIN GROUP (ORDER BY v)` | 离散百分位，`ceil(q*n)` 次序值；保留输入类型 |
| `mode` | `MODE() WITHIN GROUP (ORDER BY v)` | 最高频非 NULL 值；同频取最小值（PostgreSQL 行为） |
| `string_agg` | `LISTAGG` / `STRING_AGG` | 非 NULL 字符串按 asc/desc 稳定排序后用分隔符拼接 |

关键工程性质：

- **连续 / 离散百分位定义分离**（`src/exec/aggregate.rs`），NULL 在聚合时过滤、
  同值排序由全局摄入序号 `seq` 保证稳定（`src/exec/spill.rs`）。
- **大组外排**：内存预算只容纳一个有序缓冲，超预算即落排序 run 文件，归并阶段
  k 路流式归并；预算计入**字符串缓冲字节**（`Cell::approx_bytes`）。
- **分组倾斜不无界堆积**：归并时每分组仅保留若干常数大小的 selector，原始值
  全部在磁盘 run 上；仅 (group × agg) 计数哈希存活（即结果基数）。
- **分位参数越界执行前拒绝**：`Plan::validate_quantiles` 在摄入任何数据前运行，
  `q ∉ [0,1]`、NaN、无穷均返回 `INVALID_QUANTILE`。
- **取消 / 恢复**：`prepare()` 落盘并写 `manifest.json`（complete 标记 +
  plan_hash），归并阶段可在检查点取消；`/query/resume` 校验 plan_hash 后重跑
  只读归并，结果与未中断一致。
- **诊断**：每个响应带 `request_id`、`decision`（`accepted` / `rejected` /
  `undetermined`）与**脱敏关键状态**（行数、run 数、预算峰值等计数，绝不含数据值）。

## 目录结构

```
src/
├── batch.rs            # Arrow2 类型化批次（int64/float64/utf8，可空列）与 JSON 解码
├── plan.rs             # 逻辑查询计划 + 分位参数执行前校验 + plan_hash
├── error.rs            # 显式失败类别（INVALID_QUANTILE / CANCELLED / INVALID_RESUME …）
├── config.rs           # config/groupagg.toml + GROUPAGG_* 环境变量
├── diagnostics.rs      # 请求标识、裁决记录、脱敏
├── fixtures.rs         # 本地合成夹具（手算样本/全NULL/同频/大重复/倾斜）
├── exec/
│   ├── cells.rs        # 可空、全序、可编解码的值单元（f64 total_cmp）
│   ├── budget.rs       # 内存预算（含字符串字节）
│   ├── cancel.rs       # 协作式取消（含确定性测试钩子）
│   ├── spill.rs        # 排序 run 文件、manifest、稳定 k 路归并、恢复校验
│   ├── aggregate.rs    # cont/disc 百分位、mode、有序串接（纯函数 + 流式 selector）
│   └── mod.rs          # 执行引擎：ingest → checkpoint → 两遍归并 finalize
├── server/
│   ├── dto.rs          # 线上 DTO 与 Cell/JSON 转换
│   └── handlers.rs     # Axum 路由 / 校验入口 / 取消 / 恢复
├── lib.rs
└── main.rs             # groupagg-server 启动入口
tests/
├── common/reference.rs # 独立朴素 oracle（HashMap+排序，零被测代码复用）
├── engine_test.rs      # 引擎集成测试：手算结果 + oracle 对照 + 取消恢复
└── api_test.rs         # HTTP 端到端：状态码、失败类别、具体值、取消恢复
config/groupagg.toml    # 启动配置
examples/               # 可直接 curl 的样例请求
```

## 首次运行

需要 Rust 1.75+（开发环境为 1.98）。

```bash
cargo test                       # 全部单元 + 集成测试
cargo run --bin groupagg-server  # 默认 127.0.0.1:8080，配置见 config/groupagg.toml
```

可用环境变量覆盖：`GROUPAGG_HOST`、`GROUPAGG_PORT`、`GROUPAGG_MEMORY_BUDGET`、
`GROUPAGG_SPILL_DIR`、`GROUPAGG_CANCEL_CHECK_ROWS`、`GROUPAGG_CONFIG`。

### 快速体验

```bash
# 1) 手算偶数样本（shuffled 两批次，强制外排）
curl -s localhost:8080/query \
  -H 'content-type: application/json' \
  --data @examples/even_sample.json | jq '.data.groups, .data.stats'

# 2) 确定性地在归并阶段取消（HTTP 499, undetermined + resume_token）
curl -s localhost:8080/query \
  -H 'content-type: application/json' \
  --data @examples/cancel.json | jq '.decision, .resume_token'

# 3) 用同一计划恢复，得到确定结果
curl -s localhost:8080/query/resume \
  -H 'content-type: application/json' \
  --data @examples/resume.json | jq '.data.groups[] | select(.group.g=="heavy")'

# 4) 越界分位在执行前被拒绝
curl -s -o /dev/null -w '%{http_code}\n' localhost:8080/query \
  -H 'content-type: application/json' -d '{
    "fixture":"even_sample",
    "plan":{"group_by":["g"],"aggregations":[
      {"alias":"bad","column":"score","op":"percentile_cont","quantile":1.5}]}}'
# 400
```

## 手算参考结果（测试即按这些具体数值断言）

- 偶数样本 `score = {10,20,30,40}`：
  - `percentile_cont(0.5)`：rank = `0.5·(4−1) = 1.5` → `20 + 0.5·(30−20) = 25.0`
  - `percentile_cont(0.25)`：rank `0.75` → `17.5`
  - `percentile_disc(0.5)` on `level={1,2,3,4}`：`ceil(2)=2` → `2`（int 类型保留）
  - 同值排序稳定：run 间相等 `(key,value)` 按摄入 `seq` 归并。
- **全部 NULL** 组：四个算子全部返回 JSON `null`。
- **同频 mode**：`a,a,b,b` → `a`（最小者胜）。
- **大重复组**：900×`z` + 3×`a` → mode `z`；asc 串接以 `a,a,a` 开头，desc 以
  `a,a,a` 结尾；`percentile_disc(0.99)` of 1..=903 → `894`。
- **取消恢复**：归并首轮取消后恢复，heavy 组 0..1999 的中位数 `999.5`，且结果
  与独立 oracle 完全一致。

参考 SQL（PostgreSQL 方言，用于人工对照）：

```sql
SELECT g,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY score)      AS median_cont,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY level)      AS median_disc,
       mode() WITHIN GROUP (ORDER BY tag)                      AS tag_mode,
       string_agg(tag, '|' ORDER BY tag ASC)                   AS tag_list
FROM t GROUP BY g;
```

测试中的独立 oracle（`tests/common/reference.rs`）用 `BTreeMap` 分组、标准库
排序、直接按 SQL 定义重算，不调用任何被测函数，避免“答案由被测实现自身生成”。

## HTTP 接口

| 方法/路径 | 说明 |
|---|---|
| `GET /health` | 存活检查 |
| `GET /fixtures` | 列出内置合成夹具 |
| `POST /query` | 校验 + 执行；body 支持 `schema`+`columns`/`batches` 或 `fixture` |
| `POST /query/cancel` | 取消运行中的 query（`{"query_id": "..."}`） |
| `POST /query/resume` | 凭 query_id + **同一 plan** 恢复归并 |

请求体字段：

```jsonc
{
  "query_id": "可选，需匹配 [A-Za-z0-9_-]{1,64}",
  "fixture": "even_sample | all_nulls | mode_tie | large_repeat_group | skewed_groups",
  // 或提供 schema + batches / columns（列存 JSON，null 即 NULL）
  "cancel_after_merge_checks": 1, // 可选：确定性诊断钩子
  "plan": {
    "group_by": ["g"],
    "aggregations": [
      {"alias":"p","column":"score","op":"percentile_cont","quantile":0.5},
      {"alias":"d","column":"level","op":"percentile_disc","quantile":0.5},
      {"alias":"m","column":"tag","op":"mode"},
      {"alias":"s","column":"tag","op":"string_agg","delimiter":"|","order":"desc"}
    ]
  }
}
```

错误响应统一形如：

```json
{
  "status": "error | undetermined",
  "request_id": "req-…",
  "query_id": "…",
  "error_kind": "INVALID_QUANTILE | INVALID_REQUEST | PARSE_ERROR |
                 CANCELLED | INVALID_RESUME | BUDGET_EXCEEDED | SPILL_IO | UNSUPPORTED",
  "message": "…",
  "decision": { "verdict": "rejected|undetermined", "key_state": { …计数，无数据… } }
}
```

状态码：400（请求/分位/解析）、409（恢复计划不匹配）、499（已取消）、507（预算/
外排 I/O）。

## 外排、预算与取消恢复设计要点

- run 文件格式：`GRUN` 魔数 + 版本 + key 列数 + 定长记录
  （`seq:u64, agg:u32, desc:u8, key cells…, value cell`），Cell 自带类型标签编码。
- 每个 record 预留前先 `MemoryBudget::try_grow`；拒绝时把当前缓冲整体排序落盘、
  释放预算后重试，故峰值不超过预算（单条超预算直接 `BUDGET_EXCEEDED`）。
- `manifest.json` 原子 rename 落盘，记录 `plan_hash / runs / next_seq /
  ingested_rows / peak_memory_bytes / complete`；恢复时核对 plan_hash、key 列数、
  run 文件存在性，任何不符返回 `INVALID_RESUME`。
- 归并为两遍：pass1 只数非 NULL（决定 cont/disc 秩），pass2 流式 selector 出结果；
  两遍都是只读操作，取消后重启必然安全且确定。

## 测试命令与真实结论

```text
$ cargo test
     Running unittests src/lib.rs
test result: ok. 37 passed; 0 failed; 0 ignored
     Running tests/api_test.rs
test result: ok. 7 passed; 0 failed; 0 ignored
     Running tests/engine_test.rs
test result: ok. 11 passed; 0 failed; 0 ignored
```

合计 **55 个测试**（单元 37 + HTTP 集成 7 + 引擎集成 11），另经
`cargo fmt` 与 `cargo clippy --all-targets -- -D warnings` 零告警通过。

覆盖映射到需求点：

| 需求 | 测试 |
|---|---|
| 手算偶数样本 | `cont_hand_computed_even_sample`、`hand_computed_even_sample_median_is_two_and_a_half`、`fixture_query_returns_concrete_hand_computed_values` |
| 全部 NULL | `all_null_group_returns_null_for_every_aggregate`、`all_null_group_yields_none` |
| 同频 mode | `mode_tie_picks_smallest_and_large_repeat_group_wins`、`mode_tie_resolves_to_smallest_value` |
| 大重复组 | `large_repeat_group_mode_and_ordered_aggregation` |
| 对照独立参考实现 | `randomized_inputs_agree_with_independent_oracle`（8 轮确定性 LCG 随机数据 + `tests/common/reference.rs` 朴素 oracle）及全部 oracle 断言 |
| 外排取消恢复 | `cancellation_during_merge_then_resume_gives_identical_result`、`cancel_then_resume_over_http_finishes_the_query` |
| 分位越界执行前拒绝 | `rejects_quantile_above_one_before_execution`、`rejects_nan_quantile`、`quantile_out_of_range_is_rejected_before_ingest`、`out_of_range_quantile_is_rejected_with_category` |
| 倾斜不无界堆积 | `skewed_groups_keep_memory_bounded_and_results_correct`（断言 `peak_memory_bytes <= budget`、run 数 > 5） |
| 失败类别断言 | `resume_with_wrong_plan_is_conflict`(409 INVALID_RESUME)、`type_mismatch_and_unknown_column_are_rejected`、`unsafe_query_id_is_rejected`、`resume_rejects_incomplete_manifest` |
| 稳定同值排序 | `kway_merge_is_sorted_and_stable`（跨 run 相等值按 seq 归并） |

### 手工冒烟（真实运行输出）

```text
POST /query (even_sample)        → 200 accepted, median_cont=25.0, median_disc=2, tag_list="a|b|c|d"
POST /query (cancel hook)        → 499 CANCELLED, verdict=undetermined, resumable=true,
                                    spill_complete=true, spill_runs=306, resume_token 回传
POST /query/resume (同一 plan)   → 200, heavy 组 med=999.5, groups=201
POST /query (quantile=1.5)       → 400 INVALID_QUANTILE, verdict=rejected
POST /query (op=bogus)           → 400 INVALID_REQUEST
```

