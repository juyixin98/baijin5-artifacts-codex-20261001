# pull-query

可组合的**拉式（pull-based）**查询执行框架。算子以树组合，根算子被反复
`next()` 拉取，逐批产出 Arrow2 列式数据。包含扫描（含阻塞源）、阻塞排序
（有界内存 + 磁盘溢写 + 流式多路归并）和哈希连接（阻塞构建 / 流式探测）。

技术栈：**Rust · Axum · Arrow2**。全部数据为本地确定性合成夹具，无外部账号、
无真实业务数据、离线可运行。

---

## 1. 模块边界与数据/错误契约

| 模块 | 职责 |
|------|------|
| `src/batch.rs` | 类型化 `Batch` / `Schema` / `Scalar`、行式构造器、null 处理 |
| `src/error.rs` | 唯一错误类型 `QueryError` 与**互斥**的 `ErrorKind` 分类 |
| `src/cancel.rs` | 取消令牌、截止时间、每次拉取携带的 `Control` |
| `src/diag.rs` | 运行编号 `run_id`、算子生命周期状态、可重放结构化日志 |
| `src/resource.rs` | 内存/文件句柄计数、溢写文件、类型化 run 编解码 |
| `src/operator.rs` | 拉取 trait 与**共享生命周期状态机**（所有算子复用） |
| `src/operators/` | `scan` / `sort` / `join` / `projection` / `limit` / `failing` |
| `src/fixture.rs` | 本地合成 users / orders 数据集 |
| `src/oracle.rs` | **独立参考实现**（稳定排序 + 嵌套循环连接），测试期望值由它产生 |
| `src/exec.rs` | 请求 → 算子树 → 运行 → 结果/统计/日志 |
| `src/validate.rs` | 验证入口：扫描 / 溢写排序 / 连接 / 超时 / 取消竞态 |
| `src/http.rs` | Axum 路由（入库以便集成测试 in-process 驱动） |
| `src/bin/server.rs` | 仅负责绑定端口、启动服务 |

模块只在边界处校验、向上传递同一种 `QueryResult<T>`，没有跨层的 panic 控制流。

---

## 2. 四条核心保证

1. **取消沿算子树协作式传播；已返回批次的生命周期与执行上下文分离。**
   每次 `next()` 入口、阻塞排序每次上游拉取/每次溢写前后、连接构建每次拉取、
   阻塞源的睡眠（每 5ms 切片）都会调用 `Control::check()`。`Batch` 完全 owning，
   借用 Arrow 不可变、引用计数的列缓冲——算子失败/取消/关闭后，消费者手中
   已拿到的批次依旧可读（测试 `returned_batches_stay_readable_after_cancel`）。

2. **超时与用户取消是两个不同的错误类别。**
   `Control::check()` **先查取消令牌，再查截止时间**：二者同时触发时显式的用户
   动作报 `cancelled` 而非 `timeout`。分别由
   `timeout_is_distinct_from_cancellation`、`cancel_beats_deadline_when_both_signalled`
   断言；HTTP 上前者 408、后者 409。

3. **每算子关闭可重复且不双释放。**
   `OperatorCore` 在调用具体 `release()` **之前**就置位 `released`，因此重复
   `shutdown()`、以及从 Drop/取消展开路径重入，都至多释放一次。排序关闭时把
   归并阶段整体替换为 `Phase::Closed`，从而丢弃所有 `RunReader` 的文件句柄。
   （测试 `double_close_is_idempotent_and_releases_once`。）

4. **错误后不再消费损坏的下游流。**
   任一算子 `pull` 返回错误后进入 `Failed`，此后每次 `next()` 都返回**同一个**
   终止错误且**绝不再次进入具体算子**（用 `pulls` 计数断言），更不会再去拉它的
   上游。排序/连接在构建期遇到上游错误也立即向上返回、不再拉取。

---

## 3. 错误语义（`ErrorKind`）

| kind | 含义 | 触发示例 | HTTP |
|------|------|----------|------|
| `invalid_input` | 计划/批次/参数在边界处非法 | 未知排序列、连接键类型不符、批次列数不等 | 400 |
| `state_conflict` | 生命周期阶段错误 | close 后 `next()`、向已 seal 的 run 写入 | 409 |
| `resource_exhausted` | 内存/句柄/溢写预算耗尽，或无法建溢写文件 | 溢写路径不可创建 | 507 |
| `computation_failed` | 数据相关计算失败 | 溢写帧截断/类型标签不符、连接键越界 | 500 |
| `timeout` | 拉取源时墙钟预算到期 | 阻塞睡眠超过 deadline | 408 |
| `cancelled` | 属主显式翻转取消令牌 | 跨线程取消 / `cancel_after_ms` | 409 |

输入错误、状态冲突、资源耗尽、计算失败彼此可区分；`timeout`/`cancelled` 属于
**控制信号**而非故障（`ErrorKind::is_control()`）。错误带结构化 `Context`
（观测算子、键值字段），日志含 `run_id`、关键中间状态与判定理由，可据此重放。

---

## 4. 排序溢写

- 构建期把行缓冲的逻辑字节数计入 `ResourceTracker`；超过 `memory_budget` 即把
  当前缓冲排序、写成**一个有序 run**、释放缓冲。
- run 落盘为长度前缀的类型化帧（int / utf8 / bool + null 位图），`seal()` 立即
  归还写句柄但保留文件。
- 全部溢写时，尾批也落盘，随后对每个 run 开一个**流式 `RunReader`** 做稳定
  N 路归并；任一刻每个 run 只持有一个帧，归并内存有界，与总输入量无关。
- `RunFile` 为 `Arc` 共享，最后一个持有者 drop（或幂等 `delete()`）时物理删除，
  只删一次。

---

## 5. 运行与复现

### 前置

Rust（已在 1.98 验证）。依赖仅 `arrow2 / axum / tokio / serde / serde_json`。

### 一键本地演示

```bash
./demo.sh                 # 构建 release、起服务、依次验证各场景
PQ_PORT=9000 ./demo.sh    # 自定义端口
```

### 跑测试（实际执行并报告结果）

```bash
cargo test                # 39 个测试，全部断言具体结果/失败类别
cargo clippy --all-targets   # 0 warning
cargo fmt
```

测试覆盖：阻塞输入、排序溢写（断言 ≥3 个 run、归并结果等于独立预言机）、下游
提前停止（limit 后上游仍被关闭回收）、超时/取消竞态（取消在 ~50ms 内胜出）、
文件句柄/缓冲/溢写文件全部归零、已返回数据失败后仍可读。

> 期望值**不**由被测算子产生：`src/oracle.rs` 用另一种写法（纯行数据的稳定排序
> 与嵌套循环连接）独立计算，排序/连接测试与它逐行比对，避免“同一套算法错两次也
> 能通过”。

### 手动起服务

```bash
cargo run --release --bin pq-server        # 默认 127.0.0.1:8080，PQ_PORT 可改
```

端点：

| 方法/路径 | 说明 |
|-----------|------|
| `GET /health` | 存活检查 |
| `POST /query` | 运行单源计划（scan / sort / project / limit / timeout） |
| `POST /validate/{scan,spill_sort,join_sort,timeout,cancel}` | 固定验证场景 |
| `GET /query/stream?batches=&delay_ms=&cancel_after_ms=` | NDJSON；独立任务翻转取消令牌，演示用户取消（无 deadline，故绝不会是 timeout） |

复现关键场景：

```bash
# 溢写排序：runs_spilled>=3，open_files_after_close=0
curl -s -X POST localhost:8080/query -H 'content-type: application/json' \
  -d '{"source":{"table":"orders","rows":50},"batch_size":6,
       "sort_keys":["amount"],"sort_memory_budget_bytes":120}'

# 超时（408, kind=timeout）
curl -s -X POST localhost:8080/validate/timeout

# 用户取消竞态（409, kind=cancelled）
curl -s -X POST localhost:8080/validate/cancel

# 流式取消，末行 stats 事件 error.kind=cancelled
curl -s "localhost:8080/query/stream?batches=50&delay_ms=20&cancel_after_ms=40" | tail -1
```

`/query` 请求体字段：`source`（`{"table":"users"}` 或
`{"table":"orders","rows":N}`）、`batch_size`、`sort_keys`、
`sort_memory_budget_bytes`、`project`、`limit`、`timeout_ms`。

每个响应都带 `run_id`、资源计数与 `log`；`log` 形如：

```
=== run-000005 ===
#001 INFO  [blocking_scan] blocked — sleeping 20ms before batch 0
#002 INFO  [blocking_scan] emitting — batch 0 rows=3
#003 ERROR [blocking_scan] failed — kind=cancelled query cancelled by owner
TERMINAL Failed(Cancelled): cancelled
```

据此可用 `run_id` 重放：发生顺序、每算子中间状态（emitting/spilling/merging/
failed/closed）以及终止判定理由都在其中。

---

## 6. 目录

```
src/
  batch.rs cancel.rs diag.rs error.rs resource.rs operator.rs
  exec.rs fixture.rs oracle.rs validate.rs http.rs lib.rs
  operators/{scan,sort,join,projection,limit,failing}.rs
  bin/server.rs
tests/
  common/mod.rs batch_test.rs sort_test.rs join_test.rs
  lifecycle_test.rs exec_test.rs resource_test.rs api_test.rs
demo.sh  README.md
```
