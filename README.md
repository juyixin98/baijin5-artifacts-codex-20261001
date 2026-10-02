# ioq-runtime — 本地 IO 提交/完成队列教学运行时

一个本地、全合成的 IO 提交与完成队列（submission/completion queue）教学运行时，
演示类 io_uring 运行模型的核心契约：**取消语义**与**连接世代（connection
generation）**。技术栈：Rust + Axum + 文件系统（JSONL 日志）。所有 IO 由可控的
脚本化适配器合成，不需要任何生产账号或真实业务数据。

## 模块划分（真实职责，非单文件脚本）

| 模块 | 职责 |
|---|---|
| `src/model.rs` | 公共数据模型：`UserData` 身份、操作、最终完成记录、阶段枚举 |
| `src/handle.rs` | 连接表 + 世代计数；槽位复用时世代递增，过期句柄被拒绝 |
| `src/queue.rs` | 有界提交队列；溢出即显式背压（`QueueFull`） |
| `src/buffer.rs` | 缓冲区租约注册表；属主校验释放、双释放检测 |
| `src/adapter/` | 设备适配器 trait + 可控脚本化适配器（成功/失败/挂起/丢失/重复完成/故障注入） |
| `src/engine.rs` | 运行模型状态机：提交、取消、派发、完成路由、超时、资源释放 |
| `src/journal.rs` | 持久状态（JSONL 完成记录）+ 采样状态（定期快照）；文件/内存两种 sink |
| `src/diag.rs` | 诊断事件：接受/拒绝/无法判定 + 原因；路径脱敏（FNV-1a 哈希） |
| `src/api.rs` | Axum HTTP 接口与后台泵 |
| `src/config.rs` | TOML 配置加载 |
| `config/runtime.toml` | 运行配置 |
| `tests/engine_contracts.rs` | 15 个引擎契约测试（确定性交错） |
| `tests/api_integration.rs` | 7 个 HTTP 集成测试（tower oneshot，确定性时钟） |

## 业务与算法契约（实现位置）

1. **用户数据身份绑定一次提交** — `UserData{conn, generation, submission}` 在
   `Engine::submit` 时绑定，适配器原样回显；提交 id 全局单调、从不复用。
2. **取消请求成功 ≠ IO 未发生** — 取消被接受仅表示请求被转发
   （`CancelAccept::Forwarded`）；设备仍可能报告 `Success` 或
   `Cancelled{io_performed:true}`。最终完成记录才是权威。
3. **最终完成记录恰有一个，迟到完成不能命中新复用句柄** — `finalize` 一次性
   写入；迟到/过期世代的完成进入孤儿诊断（`OrphanCompletion`），不生成第二条
   记录，也不能认领复用槽位的新属主。
4. **队列溢出明确背压** — 提交队列满 → `SubmitError::QueueFull`（HTTP 429）；
   缓冲池耗尽 → 派发延迟（`DispatchDeferred`，Indeterminate）。
5. **资源释放等待所有关联完成** — 连接关闭进入 Draining，直到所有记录终结
   且所有租出缓冲区归还才 Closed 并允许槽位复用；超时会终结记录但**不**释放
   缓冲区（设备可能仍在写），只有设备的（迟到）完成才归还缓冲区。

## 构建、运行、测试

```bash
cargo build              # 构建（依赖已锁定于 Cargo.lock）
cargo test               # 22 个独立测试
cargo run -- config/runtime.toml   # 或 IOQ_CONFIG=path cargo run
```

## 示例调用

```bash
B=http://127.0.0.1:7878
# 打开连接（返回 conn_id 与 generation）
curl -s -X POST $B/connections -H 'content-type: application/json' -d '{}'
# 提交（201；队列满返回 429；世代过期返回 409）
curl -s -X POST $B/connections/0/submissions -H 'content-type: application/json' \
  -d '{"generation":1,"op":{"kind":"read","path":"/data/file.bin"},"request_id":"r1"}'
# 取消（202 forwarded：IO 仍可能完成；409 already_final；404 unknown）
curl -s -X POST $B/submissions/0/cancel -H 'content-type: application/json' \
  -d '{"conn":0,"generation":1}'
# 查询最终记录 / 诊断事件 / 统计
curl -s $B/submissions/0
curl -s "$B/diag/events?since=0"
curl -s $B/stats
# 关闭连接（无未完成项 → 200 closed；否则 202 draining）
curl -s -X POST $B/connections/0/close -H 'content-type: application/json' \
  -d '{"generation":1}'
```

演示操作路径（合成设备行为）：`demo:hang`（取消才完成，IO 未执行）、
`demo:hang-partial`（取消时 IO 已部分执行）、`demo:never`（丢失，走超时）、
`demo:fail`、`demo:slow`、`demo:flaky-cancel`（取消被接受但 IO 仍成功）、
`demo:double`（设备重复完成，检测双释放）。

## 验证结果（真实执行）

- `cargo test`：**22/22 通过**（15 引擎契约 + 7 API 集成）。测试断言具体结果
  与失败类别（如 `CancelError::AlreadyFinal(Success{bytes:9})`、
  `SubmitError::QueueFull{capacity:1}`、孤儿完成的 Reject 决策），并包含一个
  与引擎无关的手写参考答案（`journal_matches_handwritten_oracle`：期望值以
  字面量写死在测试中，日志 JSONL 用 serde_json 独立解析后逐条比对）。
- 真实运行服务器验证（`cargo run` + curl）：
  - 提交→完成：`{"outcome":{"type":"success","bytes":512}}`；
  - 飞行中取消 `demo:flaky-cancel`：返回 `accepted:"forwarded"`，最终记录为
    `success` 且 `cancel_requested:true`（取消成功 ≠ IO 未发生）；
  - `demo:never`：5s 后记录为 `timed_out`，连接保持 `draining`、
    `outstanding_buffers:1`（资源释放等待完成）；
  - 诊断接口对 `/home/alice/secret.txt` 只输出 `<path#52d5dea1:len22>`，
    原始路径不出现在任何事件中；
  - 日志文件 `var/journal.jsonl` 含 3 条完成记录，每提交恰一条。

## 诊断与脱敏

每个诊断事件携带：`seq`、`ts_ms`、`request_id`（调用方提供）、`subject`
（UserData）、`kind`、`decision`（accept/reject/indeterminate）与 `reason`。
无法判定的情形显式标为 indeterminate：超时（IO 是否发生不可知）、缓冲区耗尽
（重试可能成功）。路径等敏感数据在进入事件前即被脱敏为
`<path#<fnv1a哈希>:len<长度>>`。

## 剩余限制

- 单线程泵模型：无真实并行 IO，适配器为全合成（不读写真实磁盘）。
- 日志为逐行 flush 的 JSONL，**无 fsync**；崩溃可能丢失最近记录。
- 超时精度受泵间隔（`pump_interval_ms`）限制。
- 已完成记录与诊断事件驻留内存，无 GC/归档（教学规模）。
- 缓冲区池按提交数有界；`demo:never` 类丢失的 IO 会永久占用租约（这是有意
  展示的教学点：设备不归还，资源就不能释放）。
- 脱敏是哈希指纹，不是加密擦除；哈希可被撞库（对教学场景可接受）。
