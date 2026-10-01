# subquery-decorrelation

受限相关子查询（`EXISTS` / `NOT EXISTS` / 标量子查询 / 标量聚合子查询）的**去相关编译与执行服务**。

- **语言/框架**：Rust 2021 + [Axum](https://github.com/tokio-rs/axum) 0.8 HTTP 层 + [Arrow2](https://github.com/jorgecarleitao/arrow2) 0.18 类型化列式批次。
- **数据**：仅本地合成 JSON 夹具，内存目录，无任何外部业务账号或真实数据。
- **核心**：真实的"魔法集 / 分组–探测（group-probe）"去相关算子，外加一个**完全独立**的逐外行嵌套循环解释器作为对照基准。

---

## 1. 支持与拒绝的形式

| 形式 | 处理 |
|---|---|
| `EXISTS (SELECT ... WHERE inner.k = outer.k ...)` | 半连接去相关：内表按键 `GROUP BY` 一次，外表逐行探测非空组 |
| `NOT EXISTS (...)` | 反连接去相关：组为空（或不存在）→ TRUE |
| `(SELECT col FROM inner WHERE ...)` 标量 | 0 行→`NULL`；1 行→该值；**>1 行→基数冲突错误**（定位到具体外行） |
| `COUNT(*)` 标量聚合 | 空相关组 → **0**（绝不是 NULL） |
| `SUM(col)` 标量聚合（INTEGER） | 忽略 NULL 输入；空组/全 NULL 组 → **NULL**；溢出报错 |

**刻意拒绝**（在验证入口直接失败，类别 `unsupported_form`，附原因与改写建议）：

- `NOT IN` / `IN` / `ANY` / `ALL`。

`NOT IN` **不**等价于 `NOT EXISTS`：当内表值列表含 NULL 时，`x NOT IN (..., NULL, ...)` 对每个 `x` 都是 UNKNOWN（外行被丢弃），而 `NOT EXISTS` 仍保留没有匹配内记录的外行。测试 `not_in_and_not_exists_actually_diverge_under_null` 用三值逻辑直接见证这一分歧。因为半/反连接的去相关恒等式对 `NOT IN` 不成立，服务选择**拒绝并证明为什么拒绝**，而不是悄悄改写。

### 关键语义

- **保留外层重复行**：每个外行恰好产生一行输出，重复键的外行得到重复结果（不去重、不折叠）。
- **SQL 三值键比较**：分组/探测使用 `inner.k = outer.k` 的 SQL 语义；任一键为 NULL 都不匹配（EXISTS→FALSE，NOT EXISTS→TRUE，COUNT→0，SUM/标量→NULL）。
- **空组 COUNT 与 SUM 的区别**：`COUNT(*)=0`，`SUM=NULL`；全 NULL 值的非空组同样 `SUM=NULL` 而 `COUNT(*)=1`。
- **标量多行即错误**：两个执行器必须在**同一个外行下标**上报同一类别 `scalar_cardinality_violation`。

---

## 2. 模块组织（多模块后端，无硬编码演示）

```
src/
├── batch.rs      类型化批次：LogicalType / ScalarValue / Column(Arrow2 数组) / RecordBatch
├── plan.rs       查询计划 IR（SubqueryKind、相关性等式、聚合种类、执行器模式）
├── ast.rs        线上 DTO（deny_unknown_fields）与可解释响应模型
├── catalog.rs    资源与状态：类型化内存关系目录（Arc<RecordBatch>，行数上限）
├── validator.rs  验证入口：拒绝不支持形式、模式/类型检查、重写适用证明
├── executor.rs   查询算子：decorrelated（哈希分组探测）与 row_by_row（独立嵌套循环）
├── service.rs    编排：双执行器、等价性裁决、关联化 trace、失败/不确定结论分栏
├── config.rs     TOML + 环境变量配置
├── http.rs       Axum 路由/处理器/状态码映射/请求关联 id
└── bin/server.rs 服务进程
tests/
├── common/mod.rs          独立测试预言机（直接读夹具 JSON，不调用被测执行代码）
├── semantics_test.rs      语义反例 + 手写期望值 + 预言机三重断言
├── http_test.rs           走真实 Axum 路由的接口测试（含失败类别/状态码）
└── arrow_storage_test.rs  证明数据真实落在 Arrow2 数组与 validity bitmap 中
```

两个执行器**只共享** `eval_group`（"一个匹配组如何变成标量"的语义）；"如何找到组"分别用哈希探测与逐对嵌套循环独立实现。参考答案由 `tests/common/mod.rs` 中独立编写的解释器与测试文件内的字面期望值给出，**不**由被测核心生成。

---

## 3. 构建与运行

依赖已在 `Cargo.toml` 中**精确锁定关键版本**（`arrow2 =0.18.0`、`axum =0.8.9` 等），完整 114 个包的依赖树由提交的 `Cargo.lock` 锁定，且全部 crate **源码已 vendor 到 `./vendor`**（`.cargo/config.toml` 做源替换）。因此在任何机器上无需网络即可复现构建：

```bash
# 完全离线（空 CARGO_HOME 亦验证通过）
CARGO_NET_OFFLINE=true cargo build --release
CARGO_NET_OFFLINE=true cargo test
cargo clippy --all-targets -- -D warnings
cargo fmt --all
```

启动服务（默认 `127.0.0.1:8080`；可用 `config/decorr.toml` 或环境变量覆盖）：

```bash
cargo run --bin decorr-server
# 环境变量：DECORR_HOST / DECORR_PORT / DECORR_LOG_LEVEL / DECORR_JSON_LOGS / DECORR_CONFIG
```

---

## 4. 示例调用

```bash
# 1) 装入本地合成夹具
curl -sS localhost:8080/v1/fixtures \
  -H 'content-type: application/json' \
  --data @examples/fixtures.json | jq .

# 2) EXISTS（另见 examples/ 下的其他请求）
curl -sS localhost:8080/v1/query \
  -H 'content-type: application/json' \
  --data @examples/query_exists.json | jq .

# 3) 被拒绝的 NOT IN（HTTP 422, category=unsupported_form）
curl -sS -i localhost:8080/v1/query \
  -H 'content-type: application/json' \
  --data @examples/query_not_in_rejected.json
```

夹具 `orders(cust)` 含重复键 `10,10,NULL,20,20,99`；`payments` 中 cust=20 的行 `amount` 为 NULL，另有一行 cust 为 NULL、`payments_empty` 为空内表——专门覆盖 NULL/重复/空组反例。

### 请求结构

```jsonc
{
  "query_id": "可选客户端关联 id，会原样回显",
  "cross_check": true,            // 可选；缺省取配置（默认 true）
  "outer": { "relation": "orders", "select": ["o_id"] },
  "subquery": {
    "op": "exists | not_exists | scalar | scalar_aggregate",
    "inner_relation": "payments",
    "correlation": [ { "outer": "cust", "inner": "cust" } ],
    "aggregate": "count_star | sum",   // 仅 scalar_aggregate
    "value_column": "amount",          // scalar 的投影列 / sum 的求和列
    "output_column": "has_payment"
  }
}
```

### 响应的可解释性

- `request_id`（服务端 UUID）与 `query_id`（客户端）贯穿响应体与结构化日志。
- `executors[]`：两个执行器各自的 `mode / version / location / row_count / failure_category`。
- `equivalence`：去相关结果与逐外行解释器的**整行多重集**比对（列、顺序、NULL 位置、重复外行），失败路径则比对失败类别与外行下标。
- `rewrite`：所应用的代数恒等式（半连接/反连接/分组探测）、针对该计划的论证（含 NULL 与重复处理）、已核查危害、以及**明确不声称**的结论。
- `trace`：`receive → validate → rewrite → execute → cross_check` 关键步骤与处理位置（如 `src/executor.rs::decorrelated`）。
- 失败：独立的 `failure` 对象（稳定类别码、位置、原因、修复建议）；**不确定结论**单独放在 `uncertainties`，绝不与成功结果或硬失败混淆。

失败类别码：`unsupported_form` / `validation_error` / `schema_error` / `scalar_cardinality_violation` / `execution_error`。

---

## 5. 重写适用证明（要点）

- **EXISTS**：带相关条件 C 的 EXISTS 是依赖半连接；由魔法集/分组探测恒等式，等价于外表对内表按相关键 `GROUP BY` 后的左半连接——组非空即真。NULL 键不参与 SQL 等值连接；外表基数不变，故重复行保留。
- **NOT EXISTS**：对应反连接，组空/缺失即真；该恒等式**只**对 NOT EXISTS 成立，NOT IN 已在计划前拒绝。
- **标量**：按键分组的相关探查复现嵌套循环语义，前提是每组基数 ≤ 1，否则按外行抛基数冲突。
- **COUNT/SUM**：聚合在分组后只算一次；空组 `COUNT(*)=0`，`SUM=NULL`；NULL 输入被 SUM 忽略。
- 内表只扫描并分组一次、外表只扫描一次并探测——不存在逐行重跑子查询。

完整论证文本由 `validator::rewrite_proof` 逐计划生成，随每个成功响应返回。

---

## 6. 验证情况与剩余限制

已在本机真实执行（Rust 1.98.1，Linux x86_64）：

- `cargo test`：**19 个独立测试全部通过**——9 个语义反例（NULL/重复/空表/多行标量/拒绝形式）、7 个真实 Axum 路由测试、2 个 Arrow2 存储断言、1 个 200 轮随机差分测试（确定性种子，对所有算子在随机 NULL/重复/空内表下与独立 JSON 预言机逐行比对）。
- `cargo clippy --all-targets -- -D warnings`：零警告；`cargo fmt --all`：已格式化。
- 用**全新空 CARGO_HOME** 执行 `CARGO_NET_OFFLINE=true cargo test` 通过，证明只依赖 `./vendor` + `Cargo.lock` 即可离线复现。
- 真实 HTTP 端到端（`./target/release/decorr-server`，端口 8091）实际调用验证：
  - EXISTS 对 `10,10,NULL,20,20,99` 得 `true,true,false,true,true,false`（重复行保留、NULL 不匹配）；
  - NOT EXISTS 得 `false,false,true,false,false,true`（NULL 外行通过反连接）；
  - COUNT(*) 得 `2,2,0,1,1,0`，同一批 SUM(amount) 得 `150,150,NULL,NULL,NULL,NULL`——空组 0 vs NULL、全 NULL 值非空组 COUNT=1/SUM=NULL 均体现；
  - 多行标量返回 `HTTP 422 / scalar_cardinality_violation`，消息定位 `outer row 0` 且 `failure_cross_check=true`（两个执行器同类失败）；
  - NOT IN 返回 `HTTP 422 / unsupported_form`，含 NULL 语义差异说明与改写建议；
  - 空内表、`cross_check:false`、坏 JSON（400）、结构化日志按 `request_id` 关联，均已实测。

**剩余限制（也在每个响应的 `rewrite.not_claimed` 中声明）：**

- 只支持上述四种形式与等值相关合取；不支持非相关子查询、任意谓词、`IN/NOT IN/ANY/ALL`、`AVG/MIN/MAX`、相关列出现在 SELECT 等。
- 类型仅 INTEGER(i64)/TEXT/BOOLEAN；SUM 仅支持 INTEGER。
- 等价性是针对**所提交数据**用独立解释器经验核验；证明文本陈述的是通用代数恒等式，服务本身不运行形式化证明检查器。
- 内存单节点目录、无鉴权、无持久化；面向本地合成夹具场景设计。
