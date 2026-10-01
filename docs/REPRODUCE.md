# 复现手册

## 1. 环境

- Rust stable（1.98+ 验证），组件见 `rust-toolchain.toml`
- 主要依赖（锁定版本见 `Cargo.lock`）：`arrow2 0.18`、`axum 0.7`、
  `tokio 1`、`serde/serde_json 1`、`clap 4`、`thiserror 1`；测试 `tempfile 3`
- 全部数据为本地合成夹具，无外部账号/网络依赖

```bash
cargo test                       # 单元 17 + 算子集成 13 + HTTP 8 = 38
cargo clippy --all-targets       # 零警告
```

## 2. 正确性验证（独立参考，逐行多重集比对）

参考实现与被测核心**不共享任何代码**：

- `tests/common/mod.rs`：独立的类型化 CSV 解析器 + 标量模型 +
  HashMap 多重集算术（`+` / 饱和 `-` / `min` / 存在性）。
- `scripts/verify_e2e.py`：Python 独立解析 CSV、计算参考多重集，调用 CLI
  二进制（进程边界），将输出归一化后逐键比较计数。

```bash
cargo build
python3 scripts/verify_e2e.py . --rows 6000
```

已保留的结果：`docs/results/e2e-1790696805/report.json`。覆盖矩阵
**6 算子 × 3 模式（in_memory / auto 4KB 预算真实溢写 / external 强制外存）
× 2 数据集（6000 行重复+倾斜+碰撞数据、手工 edge 夹具）= 36 例，0 失败**。
关键观测（report 内逐例可查）：

- 三种模式输出行数/去重数完全相同（如 UNION ALL 12000 行/2087 distinct）；
- auto：`spills=300`、`max_resident_bytes=4131`（4096 预算，超出部分仅为
  触发检查时正在插入的单个键）、`recursions=15`；
- external：所有顶层桶强制落盘（`spills=8`，左右两侧均有）；
- in_memory：`spills=0`。

测试夹具按要求覆盖：重复行、嵌套 NULL（`(x,NULL)`/`(NULL,y)`/`(NULL,NULL)`）、
边界易碰撞字符串（`("12","3")`、`("1","23")`、`("1","23")文本型`、
含逗号/引号/UTF-8）、倾斜分区（约 30% 行落入 grp=0）。

## 3. NULL、编码、碰撞、溢出的定点断言

`cargo test -- --nocapture` 之外，关键测试名即断言点：

- `null_is_equal_to_null_but_not_to_values` / `nested_null_positions_matter`
  / `null_equality_and_nested_null_positions_are_fixed`
- `column_boundaries_cannot_be_confused` / `type_tags_disambiguate_textual_collisions`
- `colliding_buckets_keep_distinct_rows_apart`（暴力找到同桶不同键，验证仍逐行比较）
- `count_overflow_is_rejected_not_wrapped` / `union_all_overflow_across_sides_is_rejected`
- `external_mode_matches_reference_and_really_spills`（断言 `spills>0` 且驻留字节不越预算）
- `partition_depth_breach_is_its_own_failure_kind`

## 4. 失败类别必须可区分

统一错误枚举（`src/error.rs`），CLI 退出码 / HTTP 状态映射：

| 类别 | code 例 | CLI 退出码 | HTTP |
|------|---------|-----------|------|
| input | unknown_type / parse_value / column_count_mismatch / schema_mismatch / invalid_request / fixture_path_denied / not_found | 2 | 400 |
| state_conflict | run_id_conflict / unknown_run / spill_corrupted | 3 | 409 |
| resource_exhausted | memory_budget / partition_depth / count_overflow / spill_io / output_limit | 4 | 422（spill_io 为 507） |
| compute | — | 5 | 500 |

已保留的真实异常运行输出：`docs/results/error-cases/01..06_*.json`，
HTTP 会话记录：`docs/results/error-cases/07_curl_session.txt`（400/422/422/409）。
成功样本（含统计）：`success-case/intersect_all_spill.json`。

```bash
# 复现任意一种（以计数溢出为例）
cargo run -- run --left fixtures/edge_left.csv --right fixtures/edge_right.csv \
  --op UNION --qualifier all --mode in-memory --max-count 1 ; echo "exit=$?"  # 4
```

## 5. 可重放运行日志

提供 `--spill-dir` 时，每次运行写 `<spill-dir>/logs/<run_id>.jsonl`：
带 `run_id` 与单调 `seq` 的事件流——查询开始、输入批次、内存检查与
spill/reject 决策、分区递归、**ALL 的逐条计数算式**（left/right/result 与理由）、
输出批次、终局裁决（成功/错误类别与消息）。样本：
`docs/results/runlog-sample/demo-intersect.jsonl`。

服务端另可 `GET /v1/runs/<run_id>/events` 取回同一事件流。

## 6. 溢写帧完整性

帧格式：魔数 `SETOPSFRM1` → 记录（u32 键长 + 规范键 + u64 多重数）→
零长度结束标记 → FNV-1a 校验和 → 记录计数。缺标记、截断、校验不符、计数不符
均报 `state_conflict/spill_corrupted`（测试：`detects_corrupted_frame`、
`rejects_corrupt_frames`）。

## 7. 服务调用

```bash
cargo run -- serve --addr 127.0.0.1:8080
bash scripts/curl_examples.sh http://127.0.0.1:8080
```

请求体（`POST /v1/query`）：

```json
{
  "op": "EXCEPT",
  "qualifier": "all",
  "mode": "auto",
  "schema": "id:bigint,label:text",
  "left":  {"rows": [[1, "a"], [1, "a"], [2, null]], "batch_rows": 2},
  "right": {"fixture": "edge_right.csv"},
  "limits": {"memory_bytes": 4096, "partition_fanout": 4, "max_count": 18446744073709551615}
}
```

`mode`：`in_memory`（禁止溢写，超预算即 422）、`auto`（驻留优先，压力下溢写）、
`external`（顶层强制外存，用于确定性验证外存路径）。源既可以是行内 `rows`，
也可以是夹具根目录内的 `fixture`（`..` 与绝对路径在词法规范化阶段即被拒绝）。
