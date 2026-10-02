# setops — 超内存 UNION / INTERSECT / EXCEPT（DISTINCT 与 ALL）

基于 **Rust + Axum + Arrow2** 的集合运算服务。输入为带逻辑 schema 的类型化批次（typed
batches），支持嵌套类型与 NULL；数据规模可超出内存，中间结果以不可变 Arrow IPC
段文件落盘，分区表放不下时递归分裂。所有输入均为本地合成夹具，无需任何外部账号。

- **六种运算**：`UNION` / `INTERSECT` / `EXCEPT` × `ALL` / `DISTINCT`
- **多重集语义**：`UNION ALL = a + b`（拒绝溢出）、`INTERSECT ALL = min(a,b)`、
  `EXCEPT ALL = a − b`（下界 0）；`DISTINCT` 折叠为“是否出现”
- **NULL 语义**：SQL 集合相等——同类型两个 NULL 视为同一值
- **外存（out-of-core）**：哈希分区 + 缓冲有界的段落盘 + 递归分裂，输入不驻留内存
- **哈希碰撞正确**：哈希只做路由，分区内一律按**规范化行键逐字节精确比较**
- **可区分的四类失败**：输入错误(400) / 状态冲突(409) / 资源耗尽(507) / 计算失败(500)
- **可重放**：每个 run 落盘原始请求与 JSONL 运行日志（run_id、单调步骤、关键中间状态与判定理由）

## 目录结构

```text
src/
  error.rs      四类错误契约（ErrorKind → HTTP 状态码 / code 字符串）
  value.rs      动态类型值 + 逻辑 schema（int64/utf8/bool/list/struct，嵌套 NULL）
  encoding.rs   注入式行编码：列边界标记 + 每节点类型 tag + 长度前缀（可解码回行）
  arrow.rs      Arrow2 类型化批次 ⇄ 行（LargeUtf8/LargeList、validity 位图）
  resource.rs   内存/外存预算 + 计数溢出检查
  spill.rs      不可变 Arrow IPC 段（键段 / (键,计数) 结果段）与预算记账
  partition.rs  缓冲有界哈希分区与流式摄入（惰性迭代器，输入不驻留内存）
  executor.rs   分区递归分裂、六运算规则、结果段输出
  reference.rs  独立多重集参考实现（BTreeMap，供测试对照）
  runlog.rs     run 生命周期状态机 + JSONL 重放日志
  service.rs    Axum 路由、run 生命周期、流式 ingest、结果分页、错误→HTTP
  main.rs       进程入口（env 配置）
examples/larger_than_memory.rs   惰性生成 17.5 万行的超内存验收程序
tests/api_test.rs                端到端 HTTP 测试（独立 oracle 逐行计数）
tests/common/mod.rs              零新增依赖的 HTTP/1.1 测试客户端与服务夹具
fixtures/                        最小数据夹具与服务调用请求样例
docs/                            可复核的真实运行结果
```

## 构建、测试、运行

需要 Rust 工具链（在 1.98 上验证）。依赖经 `Cargo.lock` 锁定；本仓库主机通过
`rsproxy` 镜像拉取 crate（`~/.cargo/config.toml`）。

```sh
cargo build --release
cargo test                 # 28 个测试（19 单元 + 9 端到端 HTTP）
cargo clippy --all-targets -- -D warnings
cargo fmt

# 超内存验收（惰性生成，不物化输入；默认 5 万 distinct key）
cargo run --release --example larger_than_memory

# 启动服务
SETOPS_BIND=127.0.0.1:8080 SETOPS_DATA=./.setops-data ./target/release/setops
```

预算环境变量（均可省略）：`SETOPS_MEMORY_BYTES`、`SETOPS_BUFFER_BYTES`
（分区缓冲字节）、`SETOPS_TABLE_BYTES`（分区去重表字节）、`SETOPS_SPILL_BYTES`、
`SETOPS_SPILL_FILES`、`SETOPS_OUTPUT_ROWS`。也可在请求体的 `budget` 字段按 run 覆盖。

## HTTP 接口

| 方法与路径 | 作用 |
|---|---|
| `GET /health` | 存活检查 |
| `POST /execute` | 一次性执行（schema + 两侧数据同包提交） |
| `POST /runs` | 创建 run（状态 `created`），返回 fan-out 与 ingest 端点 |
| `POST /runs/{id}/ingest/{left\|right}` | 追加一个类型化批次（可重复，落盘外存） |
| `POST /runs/{id}/execute` | 排干分区、产出结果段 |
| `GET /runs/{id}` | 状态、行数、外存计数、统计 |
| `GET /runs/{id}/results?fragment=n` | 分页取回一个结果段（展开为类型化行） |
| `GET /runs/{id}/log` | 可重放 JSONL 日志 |

### 服务调用示例

```sh
# 一次性 UNION ALL（极小预算，强制外存）
curl -s -X POST 127.0.0.1:8080/execute \
  -H 'Content-Type: application/json' \
  --data @fixtures/requests/union_all.json

# 结果可能跨多个 fragment，逐页取回
curl -s '127.0.0.1:8080/runs/demo-union-all/results?fragment=1'

# 流式：先建 run，再分批喂入（两侧可任意多批，超内存）
curl -s -X POST 127.0.0.1:8080/runs -H 'Content-Type: application/json' -d '{
  "run_id":"s1","schema":{"fields":[
    {"name":"id","data_type":{"kind":"int64"},"nullable":true},
    {"name":"tag","data_type":{"kind":"utf8"},"nullable":true}]},
  "op":"intersect","quantifier":"all"}'
curl -s -X POST 127.0.0.1:8080/runs/s1/ingest/left  -d '{"rows":[[1,"a"],[1,"a"],[2,"b"]]}'
curl -s -X POST 127.0.0.1:8080/runs/s1/ingest/right -d '{"rows":[[1,"a"],[3,"c"]]}'
curl -s -X POST 127.0.0.1:8080/runs/s1/execute
```

请求体结构：`schema.fields[].data_type` 用 `{"kind":"int64|utf8|bool|list|struct"}`；
一行是“列值数组”，一个批次是“行数组”。`op ∈ union|intersect|except`，
`quantifier ∈ all|distinct`。

## 行编码：列边界、类型、长度前缀

哈希可能碰撞，因此相等性只认编码字节。编码（`src/encoding.rs`）对每个值写类型 tag，
列之间写显式列边界标记，行尾写结束标记；字符串/计数均为 **8 字节长度前缀 + 原始字节**，
因此字符串内部出现 tag/标记/NUL/任意字节都不会破坏列对齐。NULL 是独立 tag，与空串、
空列表、`false`、`0` 全部不同；`1i64`、`"1"`、`true`、`[1]`、`struct{1}` 互不可碰撞。
编码可按 schema 解码回值（struct 字段名由 schema 按位恢复），并有往返单测背书。

> 注：列边界标记 `0xFE`/行尾 `0xFF` 不可能出现在合法 UTF-8 内（它们只作为续/前导字节），
> 真正能被误判为结构符的是值 tag `0x00..0x06`，测试专门把这些字节连同“伪装的长度前缀
> +int 载荷”注入两列字符串，验证长度前缀能保持解析对齐。

## 外存算法

1. 两侧用**同一**分区函数（带递归层级盐值）把行键路由到 `fanout` 个分区；分区缓冲按
   字节预算写满即落为一个不可变 Arrow IPC 键段。
2. 对每个分区，读其两侧键段，用 `HashMap<键, (左计数,右计数)>` 按**精确键**聚合。
3. 若去重表超过 `partition_table_bytes`：若只有一个 distinct key（单键本身超预算）则
   报 `resource_exhausted/partition_key_too_large`；否则以更深层哈希**递归分裂**后重试。
4. 按六运算规则产出 `(键, 多重度)`，按 `output_buffer_rows` 写结果段；取回时再按
   multiplicity 展开为行。

摄入端 `Partitioner::ingest_iter` 接受惰性迭代器，任何时刻只保留少量分区缓冲，故可处理
远超内存的输入。`examples/larger_than_memory.rs` 在约 4 KiB 缓冲 / 64 KiB 表预算下处理
17.5 万行，产生数千个外存段与数十次递归分裂，结果逐 key 与独立闭式公式一致（见
`docs/larger_than_memory_result.txt`）。

## 失败分类（可区分）

统一错误体：`{"error":{"kind","code","message","run_id"?}}`

| kind | HTTP | 典型 code |
|---|---|---|
| `input` | 400 | `invalid_json` `type_mismatch` `row_arity` `schema_mismatch` `invalid_budget` `bad_side` |
| `state_conflict` | 409 | `run_exists` `not_finalized` `illegal_transition` |
| `resource_exhausted` | 507 | `count_overflow` `spill_byte_limit` `spill_file_limit` `partition_too_large` `partition_key_too_large` |
| `computation_failed` | 500 | IPC/Arrow 编解码、磁盘 IO 等内部错误 |
| `not_found` | 404 | `unknown_run` |

计数溢出在三处都被拒绝：单侧聚合计数、`UNION ALL` 的 `a+b`、结果总多重度。

## 验证方案（参考答案独立于被测核心）

- **独立 oracle**：`src/reference.rs` 用 `BTreeMap` 纯内存多重集实现（与引擎的分区
  `HashMap`+外存路径完全不同），端到端测试另有一份基于 JSON 字符串的独立计数；两侧仅
  共享“行相等的定义（编码）”，不共享被测的分区/聚合逻辑。
- **夹具覆盖**：重复行、嵌套 NULL（list/struct）、编码易碰撞字符串（内嵌全部 tag 字节与
  伪装长度前缀）、倾斜分区（单个重键 + 大量 distinct key）。
- **逐行计数比对**：每个运算都断言**具体结果多重集**与具体失败类别，而非“接口可调”。
- **外存与内存一致性**：同一输入在默认路径与强制极小预算（大量落盘+递归分裂）下答案一致。
- **真实运行留证**：`docs/unit_and_integration_test_results.txt`、
  `docs/http_smoke_results.txt`、`docs/larger_than_memory_result.txt`。
- **日志可重放**：`<data>/runs/<run_id>/run.jsonl` 记录 run_id、单调 step、fan-out、
  分区/落盘计数、递归分裂次数、输出行数与结论；原始请求体同目录留存（`request.json` /
  `create.json` / `batch_*.json`）。

## 最小数据夹具

- `fixtures/basic_rows.json`、`fixtures/nested_rows.json`：合成数据（含上述对抗形态）。
- `fixtures/requests/{union_all,intersect_distinct,except_all}.json`：可直接 `curl --data @`
  的自包含请求样例。
