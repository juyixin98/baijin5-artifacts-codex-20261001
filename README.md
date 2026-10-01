# decorrelate-svc

受限相关子查询的**去相关编译与执行服务**。技术栈：Rust + Axum + Arrow2。
所有数据均为请求内携带的**本地合成夹具**，无生产账号、无真实业务数据、无外部服务。

支持的相关子查询形式：

- `EXISTS (相关子查询)`
- `NOT EXISTS (相关子查询)`
- 标量**聚集**子查询：`COUNT(col)` / `SUM(col)`，与外层表达式比较
- 标量**裸**子查询：无聚集、单列投影，要求每个外层行最多匹配一行（多行报错）
- NULL 感知的 `IN (相关、非聚集子查询)`

明确**拒绝**的形式：`NOT IN`（NULL 语义不同于 `NOT EXISTS`）、连接、集合运算、
嵌套子查询、多个子查询谓词、非等值相关、HAVING/ORDER BY/LIMIT 等。

---

## 1. 它解决什么 / 关键语义

相关子查询的朴素写法是“对外层每一行重新扫描一次内表”（嵌套循环）。本服务把它
**去相关**为对内表按相关键一次性散列分组、再与外层做分组连接的批量执行，同时
严格保留 SQL 语义。引擎对每个请求同时运行两套实现并对照结果：

- `naive`：逐外行嵌套循环解释器（独立参考实现，`src/naive.rs`）；
- `rewrite`：去相关分组/连接执行器（`src/rewrite/`）。

被钉死的关键语义（测试有具体断言）：

1. **保留外层重复行**：外层不去重、不折叠，按原顺序、原多重性输出。
2. **空相关组上 COUNT 与 SUM 的区别**：
   - `COUNT(col)` 对空组/缺席组返回 `0`（且忽略列中的 NULL）；
   - `SUM(col)` 对“没有非 NULL 值”的组（含空组、缺席组、仅 NULL 组）返回 `NULL`。
3. **标量多行必须报错**：裸标量子查询对某个外层行匹配多于一行时，两个引擎都
   返回 `scalar_multiple_rows`（HTTP 422）。
4. **相关键上的 NULL**：相关条件是 `outer.col = inner.col` 的等值三值逻辑。任一侧
   NULL 都不会匹配——外层 NULL 键找不到任何组（即使内表存在 NULL 键组）。
5. **IN 的 NULL 陷阱**：无相等值但存在投影 NULL ⇒ UNKNOWN（外层行被拒绝）；
   外层值为 NULL ⇒ 直接 UNKNOWN。
6. **NOT IN ≠ NOT EXISTS**：子查询只要产生 NULL，`NOT IN` 对所有外层行退化为
   UNKNOWN；本服务直接以 `unsupported_form` 拒绝，而不是错误地改写成反连接。

每条被发射的重写都附带**适用证明**（`plan.proofs`，见 `src/rewrite/proofs.rs`）。

---

## 2. 模块组织（职责分离）

| 模块 | 职责 |
|------|------|
| `src/batch.rs` | 类型化列式批次（`Scalar`/`Schema`/`Batch`）、SQL 三值逻辑等值、Arrow2 转换 |
| `src/operators/` | 查询算子：表达式/比较求值、`COUNT/SUM` 累加器、内表散列分组表 |
| `src/resource.rs` | 资源与状态：本地合成夹具 → 校验后的类型化目录（catalog） |
| `src/validator.rs` | 验证入口：名称解析、类型检查、支持形式闸门 |
| `src/naive.rs` | 逐行嵌套循环参考解释器（独立实现） |
| `src/rewrite/` | 去相关执行器 + 重写适用证明 |
| `src/arrow_io.rs` | Arrow2 边界：结果经 Arrow2 数组序列化并回读校验 |
| `src/api.rs` | 与框架无关的编排管线（校验→编译→双执行→对照→Arrow2） |
| `src/server.rs` | Axum HTTP 传输、请求 ID 关联、结构化日志 |
| `src/config.rs` / `src/state.rs` | 配置与每请求身份 |
| `tests/` | 独立测试：等价性、拒绝类别、随机差分、真实 HTTP |

核心机制（分组、去相关、三值逻辑、聚合、基数检查）全部是真实实现，没有用
硬编码演示替代。

---

## 3. 构建与运行

需要 Rust（在 1.98.1 上验证）。依赖在 `Cargo.toml` 中以 `=` 精确锁定。

```bash
cargo build --release
# 本机默认监听 127.0.0.1:8080
DECORR_BIND=127.0.0.1:8080 DECORR_LOG=info ./target/release/decorrelate-svc
```

环境变量：`DECORR_BIND`、`DECORR_MAX_BODY_BYTES`（默认 4 MiB）、
`DECORR_LOG`（tracing filter）、`DECORR_LOCATION`（默认 `local-synthetic`）。

> 注：本仓库在共享机器上开发，`~/.cargo` 曾被并行会话争用。若遇到 cargo 全局
> 锁阻塞，可使用项目内隔离的 cargo home：
> `CARGO_HOME=$PWD/.cargo-home cargo build --offline`（首次需先在线
> `CARGO_HOME=$PWD/.cargo-home cargo fetch`）。

---

## 4. 示例调用

```bash
# EXISTS（保留重复键、拒绝外层 NULL 键）
curl -sS -X POST http://127.0.0.1:8080/query \
  -H 'content-type: application/json' -H 'x-request-id: demo-exists' \
  --data @examples/exists.json | jq .

# 标量 COUNT（空组为 0）
curl -sS -X POST http://127.0.0.1:8080/query \
  -H 'content-type: application/json' \
  --data @examples/scalar_count.json | jq .

# NOT IN —— 预期被拒绝（400 unsupported_form）
curl -sS -X POST http://127.0.0.1:8080/query \
  -H 'content-type: application/json' \
  --data @examples/not_in_rejected.json | jq .

# 裸标量多行反例 —— 预期 422 scalar_multiple_rows
curl -sS -X POST http://127.0.0.1:8080/query \
  -H 'content-type: application/json' \
  --data @examples/scalar_multi_rows.json | jq .
```

健康检查：`curl -sS http://127.0.0.1:8080/health`、`.../version`。

### 请求结构

```jsonc
{
  "options": { "mode": "crosscheck" },        // naive | rewrite | crosscheck(默认)
  "query": {
    "select": [ { "column": "o_id" } ],
    "from": { "relation": "orders", "alias": "o" },
    "where_terms": [
      // 局部谓词
      { "kind": "local", "op": "ge",
        "left":  { "kind": "column", "table": "o", "column": "want" },
        "right": { "kind": "literal", "type": "int", "value": "1" } },
      // 子查询谓词（恰好一个）
      { "kind": "exists", "negated": false, "sub": {
          "from": { "relation": "lines", "alias": "l" },
          "where_terms": [
            { "kind": "correlated",
              "left":  { "table": "o", "column": "cust" },
              "right": { "table": "l", "column": "cust" } }
          ] } }
    ]
  },
  "fixtures": { "relations": [ /* 名称、列类型(int|str)、行（null 允许） */ ] }
}
```

子查询投影二选一：`"aggregate": {"func": "count|sum", "column": {...}}`
或裸投影 `"project": {...}`（用于 IN 与裸标量）。

### 可解释的响应

响应与日志都带 `request_id`（回显客户端的 `x-request-id`，否则生成）。响应中：

- `version` / `location`：版本与处理位置；
- `steps[]`：`build_catalog → validate → compile_plan → execute_naive →
  execute_rewrite → crosscheck → arrow_encode`，每步带耗时与细节；
- `plan.algorithm`、`plan.group_count`、`plan.proofs[]`：去相关算法与适用证明；
- `result.arrow_types`：Arrow2 物理类型（如 `Int64`/`Utf8`）；
- `crosscheck`：两引擎是否一致、行数、对照维度；
- `errors[]`：**失败原因单列**（`kind` 机器可读类别、`message`、`at_step`、`engine`）；
- `uncertainties[]`：**不确定结论单列**（例如单引擎模式下“本次未与参考实现对照”）。

错误类别 → HTTP 状态：校验/类型/不支持形式 → 400；`scalar_multiple_rows`、
`numeric_overflow` → 422；内部/批次损坏 → 500。

---

## 5. 重写与等价性（证明要点）

`src/rewrite/proofs.rs` 给出逐形式论证，要点：

- **相关键划分**：相关条件是等值合取。`=` 仅在两侧非 NULL 且相等时为 TRUE，故
  分组前丢弃 NULL 键内表行，恰好划分出嵌套循环会访问到的内表行；散列查找复现
  TRUE 等值。外层按序扫描、从不折叠 ⇒ 顺序与重复多重性保持。
- **EXISTS / NOT EXISTS**：分别等价于对分组内表的左半连接（semi）/左反连接（anti）。
  外层 NULL 键与空内表都“无组”，故 EXISTS=FALSE、NOT EXISTS=TRUE。
- **标量聚集**：每外层行参与聚集的匹配行恰为其查找组；COUNT/SUM 累加器在两引擎中
  相同且按相同行序扫描，连溢出报错的外层行都一致。
- **裸标量**：0 行→NULL、1 行→该值、>1 行→报错，分组计划逐组复现。
- **IN**：在唯一匹配组内做三值成员判定；NULL 外层值立即 UNKNOWN。

---

## 6. 测试（真实执行验证）

```bash
cargo test                 # 全部：单元 + 集成
cargo test --test equivalence_test
cargo test --test rejection_test
cargo test --test differential_test
cargo test --test http_test
```

- `tests/equivalence_test.rs`：用**外层 NULL、重复键、空内表、多行标量反例**，
  对照逐外行解释执行，断言**具体结果**（具体保留哪些行）与重写前后一致。
- `tests/rejection_test.rs`：断言**失败类别**（NOT IN、非相关、限定符错误、
  类型不符、未知关系/列、多个子查询等）。
- `tests/differential_test.rs`：随机生成含重复/NULL/空组/多行省的夹具，要求两引擎
  “结果逐行相同”或“错误类别相同”，覆盖数百组用例。
- `tests/http_test.rs`：在真实临时 TCP 端口上启动 Axum，验证请求 ID 关联、
  状态码与响应信封。

**参考答案独立性**：集成测试的期望值是根据夹具定义**手工推导**的（见
`tests/common/mod.rs` 顶部表格），不是调用被测核心生成的；差分测试只把被测核心
的两个独立实现相互比对以“发现分歧”，绝对正确性由手工期望值锚定。

单元测试覆盖累加器空组 COUNT/SUM、NULL 忽略、溢出等（`src/operators/agg.rs`）。

---

## 7. 剩余限制

- 仅支持 `int`（BIGINT）与 `str`（VARCHAR）；字符串仅定义 `=`/`!=` 比较。
- 顶层恰好一个子查询谓词；相关条件必须是等值合取；内层局部谓词为比较合取。
- 聚集仅 `COUNT(col)`/`SUM(col)`（单列、无 DISTINCT）；无 MIN/MAX/AVG。
- 无连接、GROUP BY 外层分组、集合运算、嵌套子查询、ORDER BY/LIMIT/OFFSET。
- 数据来自请求体（上限默认 4 MiB），为合成夹具；无持久化与鉴权（本地服务）。
- `SUM/COUNT` 使用 `i64` 且 checked 溢出，溢出返回 `numeric_overflow`。
