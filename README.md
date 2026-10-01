# recursive-cte — 受限 `WITH RECURSIVE` 执行后端

Rust + Axum + Arrow2 实现的递归 CTE 受限子集执行引擎。支持 `UNION` 与
`UNION ALL` 的去重语义差异、基于**声明键**的路径循环标记、可配置的稳定遍历
顺序，以及深度/行数超限的**显式不完整**状态。

所有输入均为进程内合成数据（请求体内联种子行与边表），不依赖任何外部账号或
真实业务数据。

## 支持范围

实现的逻辑等价于：

```sql
WITH RECURSIVE nodes AS (
    SELECT * FROM (seed rows)
    UNION [ALL | DISTINCT]
    SELECT e.<proj...>
    FROM   nodes AS n
    JOIN   edges AS e ON n.<parent_key> = e.<edge_from>
)
SELECT *, <rendered-path>, <cycle-flag> FROM nodes;
```

| 能力 | 说明 |
|---|---|
| 基础项 | 内联种子行（base term） |
| 递归项 | 工作表 × 内联边表的**等值连接**，投影为边列引用或类型化常量 |
| 集合算符 | `UNION`（默认 `DISTINCT`，集合语义）与 `UNION ALL`（袋语义） |
| 循环标记 | 输出追加 `path`（键路径，如 `[1,2,1]`）与 `is_cycle` 布尔列；循环行**输出但不再展开**，保证含环图终止 |
| 循环键 | 由 `key_columns` 显式声明；检测只看声明键，**不**包含不断增长的路径字段，避免误判与无限增长 |
| 遍历顺序 | `bfs`（按 `(业务行, 键路径)` 稳定排序，默认）、`dfs`（LIFO，前序）、`input`（边声明顺序） |
| 类型 | `int64` / `utf8` / `boolean`；Arrow2 列式批次存储；NULL 可用于任意列 |
| 限额 | `max_depth`（种子为深度 0）、`max_rows`；超限返回明确的 `incomplete_*` 状态与部分结果 |
| 可观测 | 每次运行带 `run_id`、引擎版本、逐轮 trace（消费/产生/循环/重复/接纳行数 + 判定依据） |

### 明确不支持（关键取舍）

- 不解析任意 SQL：计划以类型化 JSON 结构提交，在验证层完成全部检查。
- 递归项仅支持**单个等值连接**（工作表 ⋈ 边表）与边列/常量投影；无子查询、
  无聚合、无外部表。
- 无隐式类型强转：JSON 数字必须是精确 `int64`，字符串不会自动转数字。
- 集合去重身份是 `(业务列, 循环标记)`，**不含路径字符串**——同业务行经不同
  路径到达在 `UNION` 下只保留首次到达；循环到达与正常到达是两个不同身份。
- 不同遍历顺序产生相同的**多重集**，差异仅在行序与 trace 形状。

## 工程结构

```
src/
  batch/        类型化批次：Value / DataType / Schema / Arrow2 RecordBatch
  plan/         查询算子：RecursivePlan / RecursiveTerm / UnionOp / PathSpec
  state/        资源与状态：WorkingTable（仅本轮新行）、Accumulated（集合去重）、
                Limits、CompletionStatus、RunRecord
  executor/     半朴素扩展循环（BFS / DFS / input-order 共用同一扩展原语）
  reference/    独立显式递归参考实现（不与被测引擎共享任何执行代码）
  api/          JSON 模型、验证入口、服务流水线、响应 DTO
  config.rs     环境变量配置层（独立、可单测）
  server.rs     Axum 路由
tests/
  recursive_semantics.rs  树/菱形多父/自环/多环/重复边 + 参考实现对照
  validation.rs           失败类别断言（validation_error / HTTP 400）
  http_api.rs             真实 Axum 路由集成测试
```

“工作表只处理本轮新行”：每轮工作表被整体 `drain`，只有经集合去重后**新接纳**
的行进入下一轮；`UNION ALL` 下重复行照样保留为多行，`UNION` 下重复身份永不
重返工作表。

## 依赖锁定

`Cargo.lock` 已提交（纯本地依赖：arrow2、axum、tokio、serde、tracing 等），
`cargo build --locked` 可复现。

## 本地启动

```bash
cargo run --locked
```

可选环境变量（配置层严格解析）：

| 变量 | 默认 | 含义 |
|---|---|---|
| `RCE_BIND_ADDR` | `127.0.0.1:8080` | 监听地址 |
| `RCE_DEFAULT_ORDER` | `bfs` | 请求未指定时的默认遍历顺序 |
| `RCE_MAX_DEPTH` | `64` | 深度的进程上限（请求只能下调） |
| `RCE_MAX_ROWS` | `100000` | 行数的进程上限 |
| `RCE_LOG` | `info` | tracing 过滤级别（JSON 日志） |

健康检查：

```bash
curl -s http://127.0.0.1:8080/health
# {"status":"ok","version":"0.1.0"}
```

## 示例请求

树（`UNION DISTINCT`，BFS）：

```bash
curl -s http://127.0.0.1:8080/execute \
  -H 'content-type: application/json' \
  -d '{
    "cte_name": "nodes",
    "union": "DISTINCT",
    "order": "bfs",
    "schema": [{"name": "id", "type": "int64"}],
    "key_columns": ["id"],
    "seed": [[1]],
    "edges": {
      "schema": [
        {"name": "from_id", "type": "int64"},
        {"name": "to_id", "type": "int64"}
      ],
      "rows": [[1,2],[1,3],[2,4],[2,5]]
    },
    "recursive": {
      "parent_key": "id",
      "edge_from": "from_id",
      "projection": [{"edge_column": "to_id"}]
    }
  }'
```

响应（节选）：

```json
{
  "outcome": "ok",
  "run_id": "run-1727...-0",
  "engine_version": "0.1.0",
  "union_op": "DISTINCT",
  "order": "bfs",
  "status": "complete",
  "complete": true,
  "columns": ["id", "path", "is_cycle"],
  "rows": [
    [1, "[1]", false],
    [2, "[1,2]", false],
    [3, "[1,3]", false],
    [4, "[1,2,4]", false],
    [5, "[1,2,5]", false]
  ],
  "row_count": 5,
  "rows_deduplicated": 0,
  "max_depth_reached": 2,
  "trace": [ {"round": 0, "depth": 0, "consumed": 0, "produced": 1, "...": "seed rows admitted"} ]
}
```

自环 + 限额（注意循环行 `is_cycle=true` 仍被返回，且超限时
`status` 明确为不完整）：

```bash
curl -s http://127.0.0.1:8080/execute -H 'content-type: application/json' -d '{
  "cte_name": "loop",
  "union": "ALL",
  "schema": [{"name":"id","type":"int64"}],
  "key_columns": ["id"],
  "seed": [[1]],
  "edges": {"schema":[{"name":"f","type":"int64"},{"name":"t","type":"int64"}],
            "rows":[[1,1],[1,2]]},
  "recursive": {"parent_key":"id","edge_from":"f",
                "projection":[{"edge_column":"t"}]},
  "limits": {"max_depth": 8, "max_rows": 1000}
}'
```

错误一律是显式信封，绝不与成功混淆：

```json
{ "outcome": "error", "category": "validation_error",
  "message": "validation error: unknown column '...'", "run_id": null }
```

更多可直接运行的样例见 `examples/requests/*.json`（用
`curl -d @examples/requests/tree.json ...`）。

## 运行测试

```bash
cargo test --locked
```

测试内容：

- **树 / 多父菱形 / 自环 / 二节点环 / 重复边**：断言具体行、路径字符串、
  循环标记与 `UNION ALL` 多重集；与 `src/reference/` 的独立显式递归
  （普通递归函数 + 邻接表，不复用引擎执行代码）交叉对照，并与手写期望值
  双重核对。
- **声明键循环检测**：业务行带回不同标签、键相同，仍必须被标记为循环。
- **遍历顺序**：BFS 稳定排序、`input` 边序、DFS 前序，且重复运行逐行一致。
- **限额**：`incomplete_max_depth` / `incomplete_max_rows` 分别断言，且 trace
  说明未展开的行数。
- **失败类别**：畸形 JSON、未知字段、类型不符、投影/连接键错误、越权限额等
  全部断言 HTTP 400 + `validation_error`。
- **HTTP**：经真实 Axum router 断言信封、版本、run_id、列与行。

日志通过 `run_id` 与请求/响应关联，输出 JSON 结构化日志，包含版本、状态、
行数与轮数。
