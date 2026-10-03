# io-queue-runtime

本地 IO 提交/完成队列教学运行时（Rust + Axum + 文件系统），演示带**取消**与**连接世代（generation）**语义的 io_uring 风格运行模型。

## 运行模型

```
caller                RuntimeCore (同步核心, 逻辑时钟)              IoAdapter
  |  submit(user_data, op)  ->  SubmitAccepted{record_id, handle}      |
  |----------------------->  SlotTable.alloc (容量有限, 满则背压)        |
  |                            BufferRegistry.lease (需要时)            |
  |                            ------------------------------------->  start(slot, gen, op)
  |  cancel(handle)  ->  CancelAccepted (仅表示"已受理请求")             |
  |----------------------->  ------------------------------------->  cancel(slot, gen)
  |                            poll(now):                             |
  |                              事件归属校验 (slot + generation)  <----  Completed/Cancelled
  |                              超时扫描 -> TimedOut + abort           |
  |                              finalize: 恰一次终态, 释放 buffer       |
  |  try_release()  ->  等待所有关联完成, 否则 ReleaseBlocked            |
```

- **Handle = (slot, generation)**。slot 复用时 generation 递增，携带旧 generation 的迟到完成无法命中新句柄，被记为 `LateCompletionStaleGeneration` 异常。
- **user_data 身份**在 submit 时与唯一 `RecordId` 绑定；诊断接口只输出其 SHA-256 截断指纹。
- **恰一次终态**：记录只在 `finalize` 一处进入终态（Success / Cancelled / TimedOut / Failed），重复完成进入异常流而非第二条记录。
- **取消语义**：`cancel` 返回 `CancelAccepted` 仅表示请求已转发；IO 可能仍然完成（`cancel_requested=true` 且 `outcome=Success` 是合法记录）。真实文件系统适配器诚实返回 `CancelAck::Unsupported`。
- **背压**：队列容量固定，溢出返回 `SubmitError::QueueFull`（HTTP 429）；缓冲池耗尽返回 `NoBuffersAvailable`，是独立的失败类别。
- **资源释放**：`try_release` 只有在所有关联完成落账后才成功，否则返回 `ReleaseBlocked::InFlight([record_id...])`。

## 模块划分

| 模块 | 职责 |
|---|---|
| `src/model.rs` | 数据模型：Handle/Generation/RecordId/OpKind/CompletionRecord |
| `src/ring.rs` | 槽位表：固定容量提交队列、世代递增、过期扫描 |
| `src/runtime.rs` | 运行模型：submit/cancel/poll/finalize、恰一次、决策日志 |
| `src/resources.rs` | 资源算法：缓冲池租约、双释放检测、释放等待 |
| `src/adapter/` | 后端边界：`ScriptedAdapter`（可控夹具）与 `FsAdapter`（真实文件系统） |
| `src/journal.rs` | 持久/采样状态：JSONL 完成日志，完成记录按 1/n 采样，异常必录 |
| `src/diag.rs` | 诊断与演示 HTTP 接口，携带记录标识与脱敏信息 |
| `src/config.rs` | TOML 配置（`config/runtime.toml`） |

## 构建与运行

```bash
cargo build
cargo run -- config/runtime.toml     # 或不带参数使用默认配置
```

### 示例调用

```bash
# 查看队列状态（槽位、世代、缓冲池）
curl -s localhost:7878/diag/state | jq

# 提交一个真实文件读（op 为 serde 外部标签枚举）
curl -s -XPOST localhost:7878/io/submit -H 'content-type: application/json' \
  -d '{"user_data": 1234, "op": {"ReadFile": {"path": "Cargo.toml"}}}'
# => {"record_id":1,"slot":0,"generation":0,"note":"accepted; ..."}

# 查看完成记录（user_data 只出现 sha256 指纹，路径只出现 basename）
curl -s localhost:7878/diag/records/1 | jq

# 取消（受理 ≠ IO 未发生；观察记录终态）
curl -s -XPOST localhost:7878/io/cancel -H 'content-type: application/json' \
  -d '{"slot":0,"generation":0}'

# 背压演示：capacity=8 时连发 9 个提交，第 9 个得到 429 queue_full
for i in $(seq 9); do
  curl -s -XPOST localhost:7878/io/submit -H 'content-type: application/json' \
    -d "{\"user_data\": $i, \"op\": {\"ReadFile\": {\"path\": \"/tmp/no-such-file-$i\"}}, \"timeout_ms\": 60000}"
  echo
done

# 决策日志与异常流
curl -s localhost:7878/diag/decisions | jq
curl -s localhost:7878/diag/anomalies | jq
curl -s localhost:7878/diag/journal | jq   # 持久化 JSONL 的尾部
```

## 测试

```bash
cargo test
```

- `tests/runtime_contracts.rs` — 完成归属、取消竞争（受理后仍成功/被取消）、超时、句柄复用下的迟到完成、重复完成、取消失败类别（UnknownHandle / StaleGeneration / AlreadyRequested）、决策日志内容。
- `tests/backpressure.rs` — 队列溢出背压、缓冲池耗尽类别、缓冲恰一次租还、Nop 不租缓冲、资源释放等待全部完成。
- `tests/diag_http.rs` — HTTP 层：标识存在、`user_data` 脱敏（响应中不出现原值）、未知记录返回可解释的 404、429 背压。
- 模块内单元测试：`ring`（世代递增/满表/重复 finalize）、`resources`（双释放/耗尽/释放等待）、`journal`（采样 1/n、异常不采样）、`config`（默认值合并）。

测试使用 `ScriptedAdapter` 脚本化交错成功、取消、超时与句柄复用；期望值全部硬编码在测试里，不由被测核心生成。

## 已知限制

- 教学实现：核心为单线程同步模型，由外部驱动循环推进；未使用真正的 io_uring/io_uring 注册缓冲。
- `FsAdapter` 的取消是诚实的 `Unsupported`（大多数平台无法取消文件 IO）；取消确认路径由 `ScriptedAdapter` 演示。
- 完成记录只在内存中保留最近 256 条（更早的查 journal 文件）；异常列表无上限。
- 演示端点不做路径沙箱与鉴权，仅限本地教学使用。
- 时钟为毫秒级逻辑时钟；超时精度受驱动循环间隔（5ms）限制。
