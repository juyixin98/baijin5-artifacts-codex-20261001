# pullq — 可组合的拉式查询执行框架

Rust + Axum + Arrow2 实现的拉式（pull-based）查询执行框架，包含扫描（scan）、
阻塞排序（blocking sort，带磁盘溢写）和哈希连接（hash join）三类算子。
所有数据来自本地确定性合成夹具（`fixtures`），无外部依赖与生产账号。

## 构建与运行

```bash
cargo build              # 编译（依赖清单见 Cargo.toml）
cargo test               # 运行全部测试（32 个，含单元与集成测试）
cargo run                # 启动服务，默认 127.0.0.1:8173
bash scripts/demo.sh     # 本地演示：排序溢写 / 连接 / 超时 / 取消 / 泄漏检查
```

服务配置（环境变量）：`PORT`（默认 8173）、`MEMORY_LIMIT_BYTES`（默认 64 MiB）、
`SPILL_DIR`（默认 `$TMPDIR/pullq-spill`）。

## 模块边界与契约

| 模块 | 职责 | 边界契约 |
|---|---|---|
| `batch` | 类型化批次 `TypedBatch`（数据契约） | 拥有全部列数据；构造时校验 schema；**不持有执行上下文**，返回后生命周期独立 |
| `error` | 错误分类 `QueryError` / `ErrorCategory` | 所有跨模块失败统一为此类型，类别可机读 |
| `cancel` | `CancelToken`（watch 通道实现） | 首次取消生效，原因不可覆盖；`check()` 同步检查 / `cancelled()` 异步等待 |
| `resources` | 内存预算 `Reservation`、溢写文件 `SpillFile`、任务跟踪 | 全部为 drop-guard：释放恰好一次，泄漏可由 `snapshot()` 观测 |
| `exec` | `ExecutionContext`：run id、截止时间观察哨 | `shutdown()` 异步等待观察哨退出，保证 `tasks_live == 0` |
| `operator` | 算子 trait + scan/sort/join | 见下节"算子协议" |
| `plan` | 计划 JSON、**验证入口**、算子树构建 | 所有 `Input` 错误在执行前产生 |
| `fixtures` | 确定性合成表（SplitMix64 种子） | 测试用它重新生成输入，参考答案由 std 独立计算 |
| `service` | Axum HTTP 入口 | 错误类别 → HTTP 状态码映射 |

## 算子协议

```
next_batch() -> Ok(Some(batch)) | Ok(None) | Err(QueryError)
close()      -> 幂等；级联到子算子
```

- **取消沿算子树传播**：一个计划共享一个 `CancelToken`，每个算子在拉取边界
  （以及阻塞中的可取消 sleep）检查它；排序/连接在阻塞消费循环中逐批检查。
- **错误毒化流**：任何 `Err` 使算子进入 `Errored` 状态，之后所有 `next_batch`
  返回 `StateConflict("poll after error")`——损坏的下游流不可能被继续消费。
- **关闭幂等、不双释放**：资源全部放在 drop-guard 里，`close()` 只是提前
  drop；第二次 `close()` 是 no-op 并返回 `Ok(())`。即使调用方不 `close`
  直接 drop 算子树，guard 也会完成同样的回收（有测试覆盖）。
- **已返回批次生命周期独立**：`TypedBatch` 不引用上下文/注册表，执行树
  销毁后仍可读取（`tests/lifecycle.rs` 逐行校验）。

## 错误语义

| 类别 (`category`) | 含义 | HTTP | 典型触发 |
|---|---|---|---|
| `input` | 验证入口拒绝的非法输入 | 400 | 未知表、键列缺失/类型错误、连接列名冲突 |
| `state_conflict` | 算子协议违例 | 409 | close 后拉取、错误后继续拉取 |
| `resource_exhausted` | 资源耗尽 | 507 | 内存预算超限（连接）、内存+溢写配额同时超限（排序）、行数上限 |
| `compute` | 计算/IO 失败 | 500 | arrow2 错误、注入的扫描失败、溢写读写失败 |
| `cancelled_user` | 用户取消 | 499 | `POST /query/{run_id}/cancel` |
| `cancelled_timeout` | 截止时间到期 | 408 | 请求携带 `timeout_ms` |

**超时与用户取消严格区分**：超时只可能由 `ExecutionContext` 的观察哨任务
以 `CancelKind::Timeout` 取消令牌产生；`CancelToken` 首次取消生效，后来的
取消不会改写原因（有竞态测试覆盖）。

## HTTP 接口

- `POST /query` — 请求体 `{ "plan": {...}, "timeout_ms"?, "max_rows"?, "memory_limit_bytes"? }`；
  成功返回 `{ run_id, status, columns, row_count, rows, truncated, elapsed_ms, metrics }`
  （`rows` 最多内嵌 500 行，`row_count` 始终精确）。
- `POST /query/{run_id}/cancel` — 用户取消运行中的查询。
- `GET /metrics` — 活动查询及各查询资源快照（内存/溢写文件/任务）。
- `GET /health`。

计划 JSON 示例（更多见 `scripts/demo.sh`）：

```json
{"op": "sort", "key": "k", "run_rows": 256,
 "input": {"op": "scan", "table": "numbers", "batches": 8, "batch_rows": 256, "seed": 42}}

{"op": "join", "build_key": "id", "probe_key": "user_id",
 "build": {"op": "scan", "table": "users"},
 "probe": {"op": "scan", "table": "orders"}}
```

扫描算子带显式测试钩子：`delay_ms_per_batch`（阻塞输入）、
`fail_at_batch`（注入计算失败）。

## 排序与连接的资源语义

- **排序**（阻塞）：先把输入全部消费成 `run_rows` 大小的有序运行段；每段先
  尝试向内存预算登记，登记失败则溢写为 Arrow IPC 流文件；溢写字节超过
  `max_spill_bytes` 配额时报 `resource_exhausted`。归并阶段按段流式读取，
  输出 1024 行一批。
- **连接**（对 build 侧阻塞）：build 侧全部物化为哈希表并计入内存预算，
  超出即 `resource_exhausted`（连接不溢写）；probe 侧流式输出。空键不匹配。

## 测试与诊断

```bash
cargo test                      # 全部 32 个测试
cargo test -- --nocapture       # 查看 tlog 运行日志
cargo test --test sort_spill    # 单跑某个集成测试
```

覆盖矩阵：

| 场景 | 文件 | 断言要点 |
|---|---|---|
| 阻塞输入 + 超时/用户取消区分 | `tests/scan_blocking.rs` | 类别分别为 `cancelled_timeout` / `cancelled_user`；取消穿越 sort 边界 |
| 排序溢写 | `tests/sort_spill.rs` | 输出与 std 排序参考完全一致；溢写计数精确；配额耗尽报 `resource_exhausted` |
| 连接正确性 | `tests/join_correctness.rs` | 与 std HashMap 参考逐行一致；空 build 侧 |
| 下游提前停止 | `tests/early_stop.rs` | close 与纯 drop 两条路径资源全回收 |
| 取消竞态 | `tests/cancel_race.rs` | 20 轮竞态只有"完成/已取消"两种干净结局；双重取消保留首个原因 |
| 错误毒化 | `tests/error_poisoning.rs` | 错误类别跨算子不变；错误后拉取一律 `state_conflict` |
| 批次生命周期 | `tests/lifecycle.rs` | 上下文销毁后批次逐行可读且与参考一致 |
| 验证入口 | `tests/validation.rs` | 四类非法计划 + 畸形批次均为 `input` |
| 文件句柄回收 | `tests/resource_reclamation.rs` | `/proc/self/fd` 前后相等（fd 为进程级，故这些测试在独立二进制内串行） |
| HTTP 语义 | `tests/service_http.rs` | 状态码映射 400/408/409→(协议级)/499/507；取消端点 |

每个测试用 `[tlog][<run_id>]` 打印运行编号、关键中间状态（内存/溢写/fd 计数、
批次进度）和判断理由，失败时可直接按 run id 重放。参考答案一律由测试内的
std 代码（`Vec::sort`、`HashMap`）基于夹具输入独立计算，不由被测算子生成。

## 复现步骤（演示）

`bash scripts/demo.sh` 依次演示：
1. 小内存预算下的排序溢写（响应 metrics 中 `spill_files_created_total > 0`、
   `spill_files_live == 0`）；
2. users ⨝ orders 哈希连接；
3. 250ms 截止时间打断 100ms/批的慢扫描 → HTTP 408 `cancelled_timeout`；
4. 按 run id 取消运行中的查询 → HTTP 499 `cancelled_user`；
5. 未知表 → HTTP 400 `input`；
6. 泄漏检查：`/metrics` 全零、溢写目录为空。
