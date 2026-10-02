# blksched — 本地块请求 deadline 与 SCAN 调度比较服务

一个纯本地的多模块后端：在同一批块请求轨迹上分别运行 **deadline** 与 **SCAN(elevator)** 两种调度器，
用**可控的合成设备模型**计算服务耗时，输出可解释的排序、成本与截止时间（deadline）等待/错过情况。
所有数据均为本地合成夹具，不依赖任何生产账号或真实业务数据。

## 模块划分（各承担实际工作）

| 模块 | 职责 |
|---|---|
| `src/model.rs` | 设备服务耗时模型（HDD 线性寻道 / SSD-like 恒定延迟），整数纳秒，完全可复现 |
| `src/request.rs` | 块请求身份：半开扇区区间 `[start, start+len)`、方向、合并后的成员身份保留 |
| `src/sched/` | 调度算法：`deadline.rs`（防饥饿规则）、`scan.rs`（电梯扫描）、`mod.rs`（合并/拆分待调度集合） |
| `src/engine.rs` | 离散事件引擎：虚拟时钟、下发/完成/取消语义、事件日志 |
| `src/cost.rs` | 运行级成本核算与可解释摘要（makespan、寻道总量、deadline 错过清单、不确定结论） |
| `src/state.rs` | 持久化：每次运行写入 `data/runs/<id>.events.jsonl` + `<id>.summary.json`，重启后可回放 |
| `src/api.rs` | Axum 诊断/REST 接口 |
| `src/trace.rs` | 轨迹模型、校验、夹具加载、带种子随机轨迹生成器（xorshift，无外部随机依赖） |
| `src/config.rs` | 配置（`config/default.json`，缺失字段回退默认值） |
| `tests/` | 独立测试：手算参考答案、守恒不变量、API 错误类别 |
| `fixtures/traces/` | 顺序 / 读写混合 / 边界取消 / 合并等轨迹夹具 |
| `scripts/demo.sh` | 本地演示脚本 |

## 运行

```bash
cargo build
cargo run --bin blksched-server        # 监听 127.0.0.1:8080
# 环境变量覆盖：BLKSCHED_CONFIG / BLKSCHED_DATA_DIR / BLKSCHED_BIND
```

## 复现步骤（测试实际执行）

```bash
cargo test          # 35 个测试：单元 + 集成 + API
./scripts/demo.sh   # 启动服务、对四种轨迹做 deadline vs scan 比较、打印事件日志与错误语义
```

当前结果：`test result: ok`（15 单元 + 20 集成/API，0 失败）。

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/health` | 健康检查 |
| GET | `/v1/version` | 版本与事件 schema 版本 |
| GET | `/v1/diagnostics/state` | 采样状态：运行数、日志目录、配置、设备模型警告 |
| POST | `/v1/runs` | 运行一条轨迹（夹具名 / `random`+种子 / 内联 ops），返回摘要 |
| POST | `/v1/runs/compare` | 同一轨迹跑两种调度器，返回双摘要与差异说明 |
| GET | `/v1/runs` / `/v1/runs/:id` / `/v1/runs/:id/events` | 列表 / 摘要 / 事件日志 |

示例：

```bash
curl -X POST localhost:8080/v1/runs/compare -H 'content-type: application/json' \
  -d '{"trace": {"name": "mixed_rw"}}'
curl -X POST localhost:8080/v1/runs -H 'content-type: application/json' \
  -d '{"trace": {"name": "random", "seed": 42, "count": 64}, "scheduler": {"kind": "deadline"}}'
```

## 错误语义

所有错误返回 `{"error": {"code": ..., "message": ...}}`，`code` 稳定可供机器判断：

| HTTP | code | 含义 | 例子 |
|---|---|---|---|
| 400 | `VALIDATION_FAILED` | 输入非法 | `len=0`、扇区区间溢出 u64、重复请求 id、未知调度器/设备类型、非法轨迹名 |
| 404 | `NOT_FOUND` | 引用不存在 | 未知轨迹夹具、未知 run id |
| 409 | `CONFLICT` | 状态冲突 | 预留（当前取消语义通过事件表达，见下） |
| 500 | `INTERNAL` | 内部错误 | 日志目录不可写、序列化失败 |

**运行内的失败不表现为 HTTP 错误，而是事件日志中的分类事件**（与请求身份关联）：

- `cancel_rejected{reason: unknown_id}` — 取消从未到达的请求；
- `cancel_rejected{reason: already_finished}` — 取消已完结的请求；
- `cancelled_after_dispatch` — 取消到达时请求已下发（见下），其数据完整性结论列入摘要的 `uncertain` 字段单列。

## 核心语义（边界）

### 扇区区间与合并身份
- 请求是半开逻辑扇区区间 `[start, start+len)`，`len > 0`，方向显式为 `read`/`write`。
- 仅**完全相邻且同方向**的请求合并（重叠不合并）；三方桥接合并支持。
- 合并项保留全部原始请求 id（`members`），每个成员获得**各自**的完成结果（`merged_with` 列出同伴）。
- 取消合并项内部的成员会把该项**拆分**回连续区间段，其余成员身份与区间不变。

### deadline 防饥饿规则（可解释，按优先级）
1. 最老的、年龄 ≥ `read_expire_ns` 的读请求（读优先于写，因读通常是阻塞等待）；
2. 最老的、年龄 ≥ `write_expire_ns` 的写请求；
3. 连续 `writes_starved` 次读下发后仍有写等待 → 强制下发最近的写；
4. 否则取离磁头最近的请求（批内贪心）。
每次下发的 `dispatched` 事件都记录命中了哪条规则（`reason` 字段）。

### 取消语义：已下发 ≠ 未下发
- **未下发**：从队列移除，成本为 0，状态 `cancelled_before_dispatch`；
- **已下发**：设备操作不可召回，继续运行到正常完成，成本**计入**，状态 `cancelled_after_dispatch`，
  并在摘要 `uncertain` 中单列“数据状态未知”。
- 同一时刻的次序确定：**完成 → 到达 → 取消 → 下发决策**。因此与下发同 tick 的取消仍然生效，
  与完成同 tick 的取消会被判为 `already_finished`。

### 服务耗时来自可控模型（非实测）
- `hdd`：`service = seek_base + seek_rate × |start − head| + transfer × len`（线性合成寻道模型）；
- `ssd_like`：`service = fixed_latency + transfer × len`（恒定延迟合成模型）。
- **两者都不是硬件实测**；每份运行摘要携带 `device_caveat`，SSD-like 明确标注
  “this is NOT an SSD measurement”，机械寻道模型的结论不得当作 SSD 实测引用。

### deadline 等待与错过
- 请求的 `deadline_ns` 是**相对到达时刻**的预算：`finish > arrival + deadline` 记为错过，
  列入摘要 `deadline_misses`；每个结果的 `wait_ns = dispatch − arrival` 给出截止等待。
- 调度器的防饥饿使用固定的 `read/write_expire` 参数，与逐请求 deadline 预算相互独立（文档与测试均区分）。

## 测试策略（参考答案不由被测核心生成）

- `tests/reference_tiny.rs`：`tiny` 轨迹的调度顺序、完成时刻、makespan、寻道总量
  **全部手算**（文件头注释给出推导），逐字面值断言；
- `tests/merge_cancel.rs`：三方合并身份保留、合并拆分、边界取消轨迹的手算结果，
  以及取消失败类别（`unknown_id` / `already_finished`）；
- `tests/deadline_fairness.rs`：过期读插队、写饥饿强制下发的手算下发序列与规则标签；
- `tests/invariants.rs`：10 个种子 × 2 调度器 × 2 设备的守恒不变量（不丢不重、时间单调、
  成本一致）——这些性质与被测实现无关；
- `tests/api_tests.rs`：真实 HTTP 往返，断言具体摘要数值与错误 `code` 类别，
  以及重启后从 JSONL 日志回放运行记录。

## 依赖清单

见 `Cargo.toml`：`axum 0.7`、`tokio 1`、`serde 1`、`serde_json 1`（dev：`tower 0.4` 用于路由测试）。
随机轨迹使用内置 xorshift 生成器，不引入随机数库。
