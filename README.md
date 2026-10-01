# tvindex — 列值位图索引 + SQL 三值逻辑过滤

用 Rust / Axum / Arrow2 实现的列值位图索引查询服务。谓词按 SQL 的
**三值逻辑（3VL, Kleene K3）** 求值，支持 `AND` / `OR` / `NOT` /
`IS NULL`，并用**版本化删除**从文档全集排除行。所有数据均为仓库内的
本地合成夹具，无需任何外部账号或真实业务数据。

核心设计目标是**边界输入不悄悄算错**，因此以下四点是一等不变量，而不是
事后测试：

1. **真/假/未知三集合在同一个文档全集内运算**，且恰好构成全集的一个划分
   （每行恰属其一，互不重叠，含尾字节）。
2. **`NOT` 绝不是对“真位图”按机器字取反**。`NOT` 在已知行内交换
   TRUE/FALSE，UNKNOWN 是不动点；否则 NULL 行会被错误地翻成 FALSE。
3. **尾字节无效位恒为零**：位图以 `u64` 打包，非整字长全集最后一个字的
   填充位在每个构造器和算子上都被清零；脏尾字节在构造时即被拒绝。
4. **版本化删除把行从全集排除，同时组合索引模式保持相容**：删除推进
   “读取头版本”，但不推进“内容代”；位图索引只在同一内容代上允许组合。

---

## 目录结构（按层组织，无单文件大泥球）

```text
src/
  error.rs                 分类错误类型（未知列/类型错/全集冲突/数据错…）
  config.rs                TOML 配置层 + 环境变量覆盖
  index/                   —— 类型化批次与索引层 ——
    bitmap.rs              定全集打包位图（尾字节清零、全集校验）
    tricolor.rs            SQL 三值逻辑 Tri / Tricolor（AND/OR/NOT/IS NULL）
    batch.rs               Arrow2 类型化批次（Chunk<Box<dyn Array>> + CSV 解码）
    value_index.rs         列值位图索引（等值位图 + 范围扫描 + NULL 掩码）
    version.rs             版本化删除（内容代 vs 读取头、as-of 可见性）
  query/                   —— 查询算子层 ——
    expr.rs                谓词 AST 与 JSON 解析（显式错误分类）
    executor.rs            绑定列索引、3VL 求值、按活行全集收缩、结构化日志
  state.rs                 资源与共享状态（清单、表、版本图、索引集）
  api.rs                   Axum 路由 + 分类错误响应（异常不伪装成功）
  verify/                  —— 独立验证入口（参考答案不来自被测核心）——
    oracle.rs              独立标量 oracle：自带 CSV/TOML/表达式解析，逐行 3VL
    runner.rs              差分对照运行器（逐行核对、精确失败类别、可关联日志）
    cases.rs               NULL 交叉/全未知/非整字长/删除等边界用例
  main.rs                  二进制：serve / verify（无第三方参数解析库）
tests/
  invariants.rs            位图/3VL/尾字节/删除不变量（具体行级断言）
  differential.rs          与独立 oracle 的差分 + 168 个布尔式穷举对照
  api.rs                   真实 TCP 端口上的 HTTP 黑盒测试
config/tvindex.toml        配置层
fixtures/                  合成夹具 + 清单 + 生成脚本 + 示例请求
```

## 三值逻辑真值表

```text
AND  T F U        OR   T F U        NOT
  T  T F U          T  T T T          T -> F
  F  F F F          F  T F U          F -> T
  U  U F U          U  T U U          U -> U
```

关键点：`UNKNOWN AND FALSE = FALSE`、`UNKNOWN OR TRUE = TRUE`；
`x IS NULL` 自身**永远不会**是 UNKNOWN（它把 UNKNOWN 折成 TRUE）。

## 版本模型

- **内容代 `content_version`**：仅在行内容被重写时推进。位图索引只在同一
  内容代上允许组合，否则报 `universe_version_mismatch`。
- **读取头 `head_version`**：删除墓碑推进它。删除**不**推进内容代，因此
  删除行从可见全集消失，而组合索引依旧相容。
- `as_of` 时间旅行：行在某版本可见，当且仅当没有 `<= as_of` 的删除墓碑，
  且其内容版本 `<= as_of`。

## 离线构建（依赖已锁定）

本仓库在受限网络环境下对齐本机预热的 cargo 镜像构建，所有关键依赖在
`Cargo.toml` 中**精确锁版本**，并提交了 `Cargo.lock`：

| crate               | 版本     |
|---------------------|----------|
| arrow2              | 0.18.0   |
| axum                | 0.7.5    |
| serde / serde_json  | 1.0.203 / 1.0.117 |
| tokio               | 1.38.1   |
| toml                | 0.8.14（仅 parse 特性） |
| tracing / subscriber | 0.1.40 / 0.3.3（最小特性，无 regex） |

```bash
# 若使用本机预热镜像（推荐，完全离线）：
export CARGO_HOME=/tmp/cargo-home-a
cargo build --offline

# 常规联网环境直接：
cargo build
```

> 说明：刻意没有使用 `clap`、arrow2 的 `io_csv`、`thiserror`、tracing 的
> `env-filter` —— 这些要么镜像中没有，要么与镜像版本组合无法编译；参数
> 解析、CSV 解码、错误 Display、日志级别过滤都用很小的本地实现替代，
> 因此功能不受影响。

## 运行独立验证（参考答案不由被测核心生成）

```bash
cargo run -- verify --root .
# suite people: 19 cases, 0 failing
# suite edge67: 8 cases, 0 failing
# verify summary: 27 cases, 0 failing
```

`src/verify/oracle.rs` 是**完全独立**的逐行标量 oracle：它自带一份独立的
CSV/TOML/表达式解析，把单元格建模为 `Option<OVal>`，用纯 `match` 逐行
计算 3VL，不经过任何位图 / Tricolor 代码。差分运行器逐行核对引擎与
oracle，并断言：活行集合一致、每行真值一致、每行恰属一类、失败时给出
具体行号与**失败类别**。

## 运行 HTTP 服务

```bash
cargo run -- serve                       # 默认 127.0.0.1:8080
TVINDEX__SERVER__PORT=8123 cargo run -- serve
```

### 请求 JSON

```json
{
  "as_of": 2,
  "where": {
    "op": "and",
    "args": [
      { "op": "cmp", "column": "age", "cmp": ">=", "value": 18 },
      { "op": "cmp", "column": "active", "cmp": "=", "value": true },
      { "op": "or", "args": [
          { "op": "is_not_null", "column": "name" },
          { "op": "not", "arg": { "op": "is_null", "column": "note" } }
      ]}
    ]
  }
}
```

算子：`cmp`（`= <> < <= > >=`）、`is_null`、`is_not_null`、
`and` / `or`（至少一个参数）、`not`。

### 示例调用

```bash
curl -s http://127.0.0.1:8080/schema

curl -s -X POST http://127.0.0.1:8080/query \
  -H 'Content-Type: application/json' \
  --data @fixtures/query_example.json
```

成功响应包含 `success:true`、`run_id`、`expr`、`as_of`、内容代/读取头、
`universe{total,live,deleted_rows}`、三类行集合 `true_rows/false_rows/
unknown_rows`、WHERE 实际选中的 `selected_rows`（仅 TRUE），以及逐行
`rows[{row,verdict,selected}]`。

### 错误响应（异常绝不返回成功）

| HTTP | error_kind                    | 触发 |
|------|-------------------------------|------|
| 400  | `invalid_query`               | 语法错、NULL 字面量、空 and/or、as_of 超前 |
| 400  | `type_error`                  | 字面量与列类型不符 |
| 404  | `unknown_column`              | 列不存在 |
| 409  | `universe_version_mismatch`   | 全集长度/内容代不相容 |
| 500  | `data_error`                  | 夹具/Arrow 数据错 |

```bash
curl -s -X POST http://127.0.0.1:8080/query -H 'Content-Type: application/json' \
  -d '{"where":{"op":"cmp","column":"age","cmp":"=","value":null}}'
# {"success":false,"error_kind":"invalid_query",
#  "message":"invalid query: cmp predicate on `age` used JSON null; ..."}
```

## 测试日志可关联性

每次查询/用例都带单调 `run_id`，日志输出：运行身份、输入表达式、
`as_of` / 内容代 / 读取头、求值进度、每个算子节点的 T/F/U 位图、活行
全集位图、判定依据（如 “FALSE dominates”“UNKNOWN fixed point”），以及
最终三类计数。失败时差分报告精确到行号并标注引擎值与 oracle 值。

```bash
RUST_LOG=debug cargo test --test differential -- --nocapture
```

## 测试

```bash
cargo test            # 42 个测试：22 单元 + 7 不变量 + 7 差分 + 6 HTTP
cargo clippy --all-targets -- -D warnings
cargo fmt --check
```

- `tests/invariants.rs`：尾字节清零、NOT 非机器字取反、九宫格真值表、
  全集冲突类型、删除不强迫 FALSE。
- `tests/differential.rs`：手写具体 T/F/U 行集合 + 168 个布尔式在 67 行
  非整字长全集上的穷举逐行对照 + 具体失败类别断言 + 时间旅行。
- `tests/api.rs`：真实监听端口上的端到端 HTTP 黑盒断言。

## 夹具

- `fixtures/people.csv` + `people.toml`：7 行，含多列 NULL 交叉、一列
  全 NULL（`note`）、行 6 在 v2 删除。
- `fixtures/edge67.csv` + `edge67.toml`：67 行（非整字长，尾字 3 个
  有效位 / 61 个填充位），`a`/`b` 两列按模数置 NULL 保证尾区 NULL 交叉，
  尾行 66 在 v2 删除。由 `fixtures/gen_edge67.py` 确定性生成。

## 已知限制

- 单列内存索引：等值走哈希位图，范围走“去重值位图并集”扫描；未做排序
  位图区间索引、压缩（Roaring）或外部/多批次存储。
- 单表、只读、启动期一次性加载；内容重写需重建全部索引（版本模型会拒绝
  跨内容代组合，但本服务不在线执行 upsert）。
- 文本范围比较为 Rust 字典序（UTF-8 码点），未实现特定 SQL collation；
  浮点遵循 IEEE-754 比较（NaN 视为无序、不匹配）。
- 未实现投影/聚合/连接，查询返回的是行分类与行号，谓词即 WHERE 布尔式。
- 非 NaN 等值键按位模式归一（`+0.0/-0.0` 同键），符合 SQL 等值语义。
