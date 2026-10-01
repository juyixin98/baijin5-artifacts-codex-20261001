# tribool-index

列值位图索引 + SQL 三值逻辑（3VL）过滤，支持 **AND / OR / NOT / IS NULL**，
技术栈 **Rust + Axum + Arrow2**。所有数据来自本地合成夹具，无外部账号依赖。

针对“正常输入看起来对、边界输入悄悄算错”，本实现逐项落实并测试：

| 风险 | 做法 | 测试 |
|---|---|---|
| 三集合不在同一全集 | TRUE/FALSE/UNKNOWN 始终是**同一个 alive 全集**的三划分；组合算子强制 alive 集合相等 | `tests/three_valued_logic.rs`、`tests/deletes.rs` |
| `NOT` 直接对真位图机器字取反 | NOT 只交换 TRUE/FALSE（均为 alive 子集），UNKNOWN 保持不变；**绝不** `!word` | `not-equality`、`not_after_delete_stays_inside_alive_universe` |
| 尾字节无效位 | `Bitmap` 构造/导入时强制把尾字节填充位清零；全集长度（非字节容量）才是边界 | `tests/bitmap_edges.rs` |
| 版本化删除与组合索引相容性 | 删除只改版本化删除掩码并 bump version；跨不同删除版本的组合返回 `DELETESET_MISMATCH` | `tests/deletes.rs` |

## 工程结构（按类型化批次 / 查询算子 / 资源状态 / 验证入口分层）

```
src/
  bits.rs      字打包位图，尾位清零，跨长度运算报错
  logic.rs     Tri3 标量三值逻辑 + TriSet 三划分（同一 alive 全集）
  batch.rs     类型化、可空列批次 -> Arrow2 PrimitiveArray/Utf8Array/BooleanArray/Chunk
  index.rs     列值位图索引（NULL 掩码 + 值字典 + 有序扫描）
  state.rs     Table/Catalog：追加、版本化删除、alive 全集
  query.rs     过滤表达式 AST（cmp/and/or/not）+ 求值 + 步骤追踪
  config.rs    分层配置（默认值 -> TOML -> 环境变量）
  api.rs       Axum 路由、JSON DTO、带具体错误码的错误响应
  bin/server.rs HTTP 服务入口
  bin/verify.rs 独立验证入口（标量 oracle 逐行对照）
tests/         独立测试层（自带标量 oracle，不依赖被测核心生成答案）
  common/mod.rs  独立 Kleene oracle（纯 Option<T> 手写真值表）
  three_valued_logic.rs  NULL 交叉 / 全未知 / IS NULL / 三划分
  bitmap_edges.rs        非整字长全集、脏尾字节、跨全集运算
  deletes.rs             版本化删除、幂等、DELETESET_MISMATCH
  api.rs                 真实 Axum HTTP 端到端 + 具体错误码
config/default.toml
```

单文件实现、仅调用壳或固定返回值均不满足要求——每个模块包含真实逻辑与边界处理。

## 快速开始

```bash
# 编译（关键依赖在 Cargo.toml 用 “=” 锁定，Cargo.lock 固定传递依赖）
cargo build --release

# 独立验证：合成夹具（13 行、非 8 整除、含 NULL）逐行对照独立标量 oracle
cargo run --bin verify

# 全部单元 + 集成测试
cargo test

# 启动 HTTP 服务（默认 127.0.0.1:8080）
cargo run --bin server
# 可选：cargo run --bin server -- config/default.toml
```

## 示例调用

建表（13 行，含 NULL，非整字长全集）：

```bash
curl -s -H 'Content-Type: application/json' localhost:8080/tables -d '{
  "name": "people",
  "columns": [
    {"name": "city",   "type": "text"},
    {"name": "age",    "type": "int"},
    {"name": "active", "type": "bool"}
  ],
  "rows": [
    {"city":"Beijing","age":25,"active":true},
    {"city":"Beijing","age":null,"active":false},
    {"city":"Shanghai","age":40,"active":null},
    {"city":"Beijing","age":17,"active":true}
  ]
}'
```

查询：`age >= 18 AND active = true`（active 为 NULL 的行得到 UNKNOWN，而非 FALSE）：

```bash
curl -s -H 'Content-Type: application/json' localhost:8080/tables/people/query -d '{
  "filter": {
    "op": "and",
    "args": [
      {"op":"cmp","column":"age","cmp":">=","value":18},
      {"op":"cmp","column":"active","cmp":"=","value":true}
    ]
  }
}'
```

响应包含三类计数、匹配行 id、运行身份、所依据的表版本与每一步判定依据：

```json
{
  "ok": true,
  "run_id": "…uuid…",
  "table": "people",
  "version": 1,
  "counts": { "true": 1, "false": 2, "unknown": 1 },
  "matched_ids": [0],
  "trace": [
    {"depth":1,"expr":"age >= 18","true_rows":2,"false_rows":1,"unknown_rows":1,
     "basis":"column index over 4 alive rows; NULL rows -> UNKNOWN, deleted rows excluded"},
    {"depth":0,"expr":"AND(...)","true_rows":1,"false_rows":2,"unknown_rows":1,
     "basis":"TRUE only if all TRUE; FALSE if any FALSE; else UNKNOWN"}
  ]
}
```

`NOT` 语义（NOT UNKNOWN = UNKNOWN，且删除行/尾填充位永远不出现）：

```bash
curl -s -H 'Content-Type: application/json' localhost:8080/tables/people/query -d '{
  "filter": {"op":"not","arg":{"op":"cmp","column":"age","cmp":"=","value":40}}
}'
```

IS NULL / IS NOT NULL：

```bash
curl -s -H 'Content-Type: application/json' localhost:8080/tables/people/query -d '{"filter":{"op":"cmp","column":"age","cmp":"is_null"}}'
```

版本化删除（幂等，版本单调递增；删除行从 T/F/U 三个集合同时消失）：

```bash
curl -s -H 'Content-Type: application/json' localhost:8080/tables/people/delete -d '{"ids":[2,3]}'
```

追加行（全集增长、删除掩码保留、索引重建）：

```bash
curl -s -H 'Content-Type: application/json' localhost:8080/tables/people/rows -d '{"rows":[{"city":"Shenzhen","age":50,"active":null}]}'
```

## 三值逻辑语义（Kleene）

| A | B | A AND B | A OR B | | X | NOT X |
|---|---|---|---|---|---|---|
| T | T | T | T | | T | F |
| T | F | F | T | | F | T |
| T | U | U | T | | U | U |
| F | U | F | U | | | |
| U | U | U | U | | | |

- 比较算子（`= != < <= > >=`）遇到 NULL 操作数 -> UNKNOWN；
- `IS NULL` 自身不产生 UNKNOWN（NULL 行 TRUE，其余 alive 行 FALSE）；
- SQL `WHERE` 只保留 TRUE，UNKNOWN 与 FALSE 都被拒绝（响应仍分别报告计数）。

## 错误处理：不把未知/异常吞成成功

每个错误都有稳定错误码与 HTTP 状态，并带独立 `run_id`：

| code | HTTP | 触发示例 |
|---|---|---|
| `NOT_FOUND` | 404 | 表/列不存在 |
| `TYPE_MISMATCH` | 400 | int 列与文本字面量比较；导入行类型不符 |
| `UNSUPPORTED_OPERATOR` | 400 | 未知比较符；bool 上做 `<` |
| `INVALID_INPUT` | 400 | 删除越界 id、空 AND/OR、is_null 带值、坏 JSON |
| `UNIVERSE_MISMATCH` | 409 | 不同全集长度的位图/索引参与运算 |
| `DELETESET_MISMATCH` | 409 | 组合建立在不同版本删除集上的索引 |
| `BITMAP_SHAPE` | 409 | 位图字节长度与位数不符 |
| `SCHEMA_MISMATCH` | 409 | 追加批次模式与原表不同 |

## 测试的独立性说明

- 预期答案由 `tests/common/mod.rs` 与 `src/bin/verify.rs` 中**独立的标量 oracle**
  生成：仅用 `Option<T>` 和手写 Kleene 真值表，不调用 `TriSet`/`Bitmap`，
  因此答案不是被测核心自己产出的。
- 每个测试断言**具体行集合/计数**和**具体失败类别**（如
  `ErrorKind::DeletesetMismatch`、HTTP `409 DELETESET_MISMATCH`），
  而不是只检查“接口能调用”。
- 日志可关联输入与运行身份（`run_id`），显示表版本、每步 T/F/U 计数及判定依据。

## 配置

默认值 -> `config/default.toml` -> 环境变量：

| 环境变量 | 含义 | 默认 |
|---|---|---|
| `TRIBOOL_HOST` / `TRIBOOL_PORT` | 监听地址 | 127.0.0.1 / 8080 |
| `TRIBOOL_LOG_LEVEL` | 日志级别 | info |
| `TRIBOOL_INCLUDE_TRACE` | 响应是否含步骤追踪 | true |
| `TRIBOOL_MAX_EXPR_NODES` | 单次查询最大表达式节点数 | 10000 |

## 剩余限制（如实说明）

- 纯内存存储：进程重启后数据丢失；无持久化/WAL。
- 单列谓词为等值字典 + 有序扫描，未做区间位图压缩/ Roaring 等编码。
- 仅支持 `int64 / utf8 / boolean` 三种列类型；无 join、聚合、投影，
  查询只做过滤并返回行 id 与三值计数。
- 全表锁粒度：写入与查询通过 `RwLock` 串行化，适合演示与中小规模夹具。
- Arrow2 用于列批次的类型化表示（schema/Chunk/validity），当前未做 Arrow IPC 落盘。
