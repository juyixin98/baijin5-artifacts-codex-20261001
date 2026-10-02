# ARC 页替换引擎（本地页访问回放）

一个可审查的 **ARC（Adaptive Replacement Cache）页替换后端**实现，面向本地页
访问回放：真实列表 **T1/T2**、幽灵目录 **B1/B2**、随命中来源自适应的目标
**p**、脏页经**明确的回写适配器**落盘，并带**请求标识**的决策诊断。

技术栈：Rust + Axum + 文件系统（合成数据，无任何真实业务数据或外部账号）。

---

## 1. 运行模型（模块职责）

代码按“真实职责”拆分，不是单文件脚本，也不是只有接口的空工程：

| 模块 | 职责 |
|------|------|
| `src/types.rs` | 页标识、访问类型、命中来源、结果、统计等共享数据模型 |
| `src/arc.rs` | **纯算法**：T1/T2/B1/B2 + 自适应 p；`plan()→commit()` 分离，不感知存储 |
| `src/engine.rs` | 协调算法与 I/O：回写脏页、装入新页、零容量穿透、动态缩容、失败原子性 |
| `src/storage.rs` | **唯一 I/O 边界**：`PageStore` trait、文件系统实现、可注入故障的内存实现 |
| `src/trace.rs` | 本地合成轨迹（扫描污染 / 热点切换 / 固定混合）与 JSONL 读写 |
| `src/state.rs` | 跨进程**采样状态**（只存 p 与统计），原子落盘 |
| `src/diagnostics.rs` | 带请求标识的决策记录（接受/拒绝/无法判定）与载荷脱敏 |
| `src/api.rs` | Axum HTTP 接口（回放、缩容、统计、列表、单页状态、诊断） |
| `src/config.rs` | 外部 TOML 配置解析与校验（独立于代码） |
| `src/main.rs` | 服务入口、采样状态恢复、关停时回写脏页并持久化 |

### 单次访问的处理顺序（关键：回写失败不改变缓存状态）

```text
1. c == 0        → 穿透后端，计入 rejected_zero_capacity，诊断记为“拒绝”
2. cache.plan()  → 纯计算动作计划（不修改任何状态）
3. 受害者是脏页？→ 经 PageStore::write_back 回写（按配置重试；永久失败不重试）
                   失败 ⇒ 丢弃计划，受害者仍驻留且仍脏，请求页未装入，返回错误
4. 幽灵命中/缺页 → PageStore::fetch 重新装入（幽灵命中也必须装入）
5. cache.commit()→ 列表、命中统计、p 才真正更新
6. 写访问        → 修改驻留内容并标脏
```

---

## 2. 资源算法（ARC）与大小约束

四列表（LRU 在首、MRU 在尾）：

| 列表 | 含义 | 持有数据 |
|------|------|----------|
| T1 | 近期只访问过一次的页 | 是 |
| T2 | 至少访问过两次的页 | 是 |
| B1 | 从 T1 淘汰页的幽灵目录 | **否，仅元数据** |
| B2 | 从 T2 淘汰页的幽灵目录 | **否，仅元数据** |

任何操作后都成立（测试逐步断言）：

```text
|T1| + |T2| ≤ c
|T1| + |B1| ≤ c
|T2| + |B2| ≤ 2c
|T1| + |T2| + |B1| + |B2| ≤ 2c
0 ≤ p ≤ c
```

自适应 `p`（T1 的目标大小）**随命中来源调整**，且 REPLACE 使用调整后的新 p：

- B1 幽灵命中：`p = min(p + max(⌊|B2|/|B1|⌋, 1), c)`（向 T1 倾斜）
- B2 幽灵命中：`p = max(p − max(⌊|B1|/|B2|⌋, 1), 0)`（向 T2 倾斜）

> **幽灵命中 ≠ 数据已缓存。** B1/B2 只有 `PageId`。幽灵命中时数据不在真实
> 缓存，必须重新 `fetch`；结果计入 `ghost_hits_b1/b2`，**不计入** `hits`。
> `HitSource::is_resident()` 只有 T1/T2 为真。

### 容量为零（有定义）

`c = 0` 是合法配置：缓存层关闭。每次读穿透 `fetch`、每次写穿透直写
`write_back`，`rejected_zero_capacity` 递增，诊断结论为
`rejected / zero_capacity_passthrough`，`resident` 恒为 0。算法层在 c=0 时
也自洽：miss 只计数、不把页放入任何列表。

### 动态缩容（有定义）

`resize(new_c)` 用纯函数 `resize_plan()` 先模拟：受害者**总是**先进幽灵目录，
再按 `|T1|+|B1| ≤ new_c` 与总数 `≤ 2·new_c` 从幽灵 LRU 端裁剪。引擎逐个回写
脏受害者，**任一失败即整体中止**（容量、列表、驻留映射保持不变）；全部成功
后才 `commit_resize()`。支持缩到 0（先回写全部驻留脏页）。

---

## 3. 持久 / 采样状态

`var/<data_dir>/state.json` 只持久化**元数据**：容量 `c`、自适应目标 `p`、
各列表长度与命中/回写计数。**不持久化页内容，也不持久化列表成员**——重启后
真实/幽灵列表冷启动，仅恢复 p。落盘采用临时文件 + `rename` 原子替换。
关停（SIGINT）时先 `flush_dirty()` 回写所有脏页，再保存状态。

页内容落在 `var/<data_dir>/pages/<id>.page`（每页一文件，回写同样 tmp+rename）。

---

## 4. 诊断接口

每个决策一条 `DecisionRecord`，带：

- `request_id`：取自请求头 `X-Request-Id`，否则自动 `req-00000001`；
- `verdict`：`accepted` / `rejected` / `undetermined`；
- `reason_code`：如 `writeback_failed`、`zero_capacity_passthrough`、
  `fetch_failed`、`resize_aborted`；
- `detail`：**为什么**接受/拒绝/无法判定（幽灵命中会明确写出
  “metadata only, page re-fetched from store”）；
- `state`：决策时刻的 c/p/四列表长度/驻留数。

诊断**绝不记录页内容**；载荷展示只能经 `redact_payload()`，形如
`<redacted 15 bytes; head: 73 65 6e 73 …>`。诊断缓冲为固定容量环形，避免长跑
无限增长。

---

## 5. 构建与运行

需要 Rust（开发用 1.98）。依赖版本由 `Cargo.lock` 锁定：
axum 0.8.9、tokio 1.53.1、indexmap 2.14.2、serde 1.0.229 等。

```bash
cargo build --release

# 标准容量（config/arc.toml: c=8）
./target/release/arc-page-cache --config config/arc.toml

# 容量为零（缓存关闭）
./target/release/arc-page-cache --config config/arc-zero.toml

# 生成合成轨迹夹具
cargo run --example gen-fixtures -- fixtures
```

### HTTP 示例调用

```bash
B=http://127.0.0.1:8080

# 单次读
curl -s -X POST $B/access -H 'Content-Type: application/json' \
  -d '{"page":0,"kind":"read"}' -H 'X-Request-Id: demo-1'

# 写（产生脏页）
curl -s -X POST $B/access -H 'Content-Type: application/json' \
  -d '{"page":6,"kind":"write"}'

# 批量回放（逐步返回 outcome 与每步 stats）
curl -s -X POST $B/replay -H 'Content-Type: application/json' -d '{
  "accesses": [
    {"page":0,"kind":"read"},
    {"page":1,"kind":"read"},
    {"page":0,"kind":"read"},
    {"page":2,"kind":"write"}
  ]}'

# 动态缩容
curl -s -X POST $B/resize -H 'Content-Type: application/json' -d '{"capacity":2}'

# 统计 / 列表成员 / 单页状态
curl -s $B/stats
curl -s $B/lists
curl -s $B/page/0

# 决策诊断（按请求标识或最近 N 条）
curl -s "$B/diagnostics?request_id=demo-1"
curl -s "$B/diagnostics?limit=20"
```

所有响应使用统一信封 `{success, request_id, data, error}`。回写/装入失败返回
502 与具体错误码（`writeback_failed` / `fetch_failed` /
`resize_writeback_failed`），并在诊断中保留拒绝轨迹。

### 配置文件（`config/arc.toml`）

```toml
[cache]
capacity = 8          # 0 合法：缓存关闭
page_size = 4096
writeback_retries = 1 # 瞬时失败的额外重试次数；永久失败不重试

[server]
bind = "127.0.0.1:8080"
data_dir = "./var/arc-cache"
```

---

## 6. 测试如何组织（答案不是被测核心自己生成的）

| 测试文件 | 内容 |
|----------|------|
| `tests/arc_reference.rs` | 与**独立参考模型**逐步对照 |
| `tests/support/reference.rs` | 独立 ARC：只用标准库 `VecDeque/HashSet`，逐字转写论文伪代码，**不 import 生产算法** |
| `tests/engine_behavior.rs` | 幽灵非驻留、扫描污染、热点 p 方向、脏页回写、失败类别轨迹、零容量、缩容 |
| `tests/api_http.rs` | 真实 axum 服务器 + 裸 HTTP 请求的端到端测试 |
| `tests/config_state.rs` | 配置校验、采样状态往返/原子性、脱敏、文件后端、轨迹 JSONL |

独立测试断言**具体结果与失败类别**，而不是“接口能调用”：

- 逐步比对 `source / evicted / p / T1/T2/B1/B2 的 LRU→MRU 内容`；
- 断言每一步的全部大小约束；
- 断言幽灵命中 `fetches +1`、`hits` 不变、随后才驻留 T2；
- 断言瞬时失败按 `writeback_retries` 重试、永久失败只尝试一次；
- 断言回写失败后 `pages_lru_to_mru()` 与失败前**完全相等**；
- 断言缩容失败后容量与列表不变。

运行：

```bash
cargo test                 # 全部 32 个测试
cargo clippy --all-targets # 零告警
cargo fmt
```

> **对照测试确实抓到过生产实现的一个真实算法缺陷**：初版在 B1/B2 幽灵命中时，
> REPLACE 仍使用调整**前**的旧 `p`（论文要求先用新 `p` 再 REPLACE）。在一条
> 6000+ 步的伪随机轨迹上，独立模型与生产选出不同受害者（页 1 vs 页 6）从而
> 暴露分歧；修复后全部对照通过。这说明参考模型是独立、有效的裁判。

---

## 7. 已真实执行的验证

- `cargo test`：32/32 通过；`cargo clippy --all-targets`：无告警；`cargo fmt` 已应用。
- 真实启动二进制，curl 实测：
  - 建立热点后扫描 100..110，热点 0/1 始终在 T2，扫描页只在 T1/B1；
  - 访问 B1 中的页 108 返回 `source=ghost_b1`、`resident_after=true`、
    `p 0→1`，并重新 `fetch`；
  - 写脏页 200 → `dirty_pages=[200]`；动态缩容 4→2 淘汰干净页并裁剪幽灵；
  - 诊断按 `X-Request-Id` 精确回溯，记录“metadata only, re-fetched”，无内容泄漏；
  - SIGINT 关停时脏页回写（文件首字节 `00→01`，`writebacks=1`），state.json 落盘；
  - 用相同 data_dir 重启恢复 `p=1`，列表冷启动（resident=0）。

---

## 8. 剩余限制（如实说明）

1. **冷启动不恢复列表/脏集合**：仅恢复 p 与统计。重启后“哪些页脏”未知，因此
   重启不会自动补回写；设计上把页内容与目录都视为可由后端重建的缓存。
2. **引擎为单 Mutex 串行处理**：HTTP 层无并发分片。回放语义清晰，但没有做高
   并发吞吐优化；`PageStore` 为同步 I/O。
3. **故障注入在内存后端**：`FilePageStore` 的回写失败依赖真实文件系统错误，
   未额外注入；失败轨迹测试通过 `FaultStore`/测试专用 store 精确构造。
4. **页内容为合成确定性数据**：用于验证机制，不代表真实磁盘布局（无分页稀疏
   文件、无校验和）。
5. **无鉴权/TLS**：诊断接口仅绑定回环地址，面向本地复核，不要直接暴露公网。
6. **缩容语义是本实现的明确定义**（受害者先进幽灵再从 LRU 裁剪）；论文主要
   描述固定 c 的 ARC，缩容的对照测试断言大小约束与回写原子性，而非某个外部
   权威的缩容伪代码。
