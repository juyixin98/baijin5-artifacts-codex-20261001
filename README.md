# arc-cache — ARC 页面替换引擎

基于 Megiddo & Modha 的 ARC（Adaptive Replacement Cache，FAST '03）实现的
本地页面缓存后端：Rust + Axum + 文件系统页存储，支持本地页访问回放、
脏页显式回写、动态容量调整、快照持久化与可审计的诊断日志。所有数据均
为本地合成夹具，不依赖任何生产账号或外部服务。

## 模块划分（真实职责）

| 模块 | 职责 |
|---|---|
| `src/arc.rs` | 核心替换算法：T1/T2 常驻列表、B1/B2 幽灵列表、自适应参数 p、淘汰计划、缩容。所有 I/O 均通过注入的 trait 完成 |
| `src/engine.rs` | 运行模型：驱动请求（单发/回放），分配请求 ID，为每个请求记录诊断决策，采样自适应状态 |
| `src/store.rs` | 后备页存储抽象 + 文件系统实现（`page-<id>.bin`）+ 可注入故障的内存实现 |
| `src/writeback.rs` | 显式回写适配器 trait；脏页只能经由此接口离开缓存 |
| `src/state.rs` | 持久化快照（仅元数据）与采样状态历史（环形缓冲） |
| `src/diag.rs` | 诊断决策记录：接受 / 拒绝 / 无法判定，键脱敏（SHA-256 指纹） |
| `src/api.rs` | Axum HTTP 接口（薄层，决策都在 engine） |
| `src/config.rs` | TOML 配置，全部字段有默认值 |
| `src/main.rs` | CLI：`serve` / `replay` / `init-pages` |
| `tests/common/model.rs` | **独立参考模型**：与核心实现零共享代码的朴素 ARC，用于逐步对照 |
| `tests/*.rs` | 手算轨迹测试、模型对照（parity）、API 冒烟、快照、诊断 |

## 算法语义（与论文的关键对应关系）

列表：T1（最近引用一次的常驻页）、T2（引用两次以上的常驻页）、
B1/B2（对应幽灵列表，**只存元数据不存数据**）。

不变量（每次变更后 `debug_assert` 校验）：

- `|T1| + |T2| ≤ c`（常驻页不超容量）
- `|T1| + |B1| ≤ c`（L1 不超容量）
- `|T1|+|T2|+|B1|+|B2| ≤ 2c`（全部历史不超 2c）
- `0 ≤ p ≤ c`

自适应：B1 幽灵命中 `p = min(c, p + max(|B2|/|B1|, 1))`；B2 幽灵命中
`p = p − max(|B1|/|B2|, 1)`（饱和到 0），整数除法。

明确的语义决策（实现与测试共同遵守）：

1. **幽灵命中 ≠ 数据已缓存**：B1/B2 命中必须从后备存储取数
   （`fetched=true`），单独计数 `ghost_hits_b1/b2`，不计入 hits。
2. 论文情形 IV(i) 的 else 分支（`|T1| == c` 且 B1 为空）：T1 的 LRU 页
   **整体丢弃，不进入 B1**（否则违反 `|L1| ≤ c`）。
3. **容量为零有定义**：读为 read-through（直读存储、不缓存），写为
   write-through（直接经回写适配器落盘），任何列表都不触碰。
4. **扩容角落规则**（仅动态扩容后出现）：未命中时只有当常驻集真的满
   （`|T1|+|T2| ≥ c`）才淘汰常驻页。固定容量下该守卫是空操作（常驻集
   一旦满就保持满），只在扩容后幽灵列表非空时改变行为。
5. **脏页淘汰**：淘汰前先计算受害者计划，脏受害者经 `Writeback`
   适配器回写，**全部成功后才变更任何列表**；回写失败则整个访问以
   `writeback` 类错误被拒绝，缓存状态（含 p）完全不变。
6. **缩容**：先按 REPLACE 偏好裁常驻页（T1 超出 p 的部分优先），再裁
   B1 恢复 `|L1| ≤ c`，再裁 B2 恢复总量 `≤ 2c`；脏页回写失败则缩容
   原子失败，容量保持原值。缩到 0 即全部回写并清空。

## 诊断与脱敏

每个请求产生一条 `DiagRecord`：`seq`、`request_id`（调用方可指定，
否则生成 `req-<seq>`）、操作、脱敏键、决策、理由、p 与四个列表大小
的前后快照。决策分三类：

- `accepted`（含 outcome：hit_t1 / hit_t2 / ghost_hit_b1 / ghost_hit_b2 /
  miss_fill / read_through / write_through）
- `rejected`（含类别：not_found / writeback / bad_request —— 确定性拒绝）
- `undecidable`（后备存储 I/O 失败，无法判定）

页 ID 视为潜在敏感：默认记录 `pg#<sha256 前 12 位 hex>` 指纹，配置
`log_raw_keys = true` 才记录原始 ID。

## 构建与运行

```bash
cargo build --release          # 依赖已锁定（Cargo.lock）
cargo test                     # 全部测试（含独立模型对照）

# 1. 生成本地页面夹具（确定性内容）
./target/release/arc-cache init-pages --dir fixtures/pages --pages 16 --size 64

# 2. 离线回放轨迹（capacity=2 时复现 README 中的扫描抗性轨迹）
./target/release/arc-cache replay --config config/example.toml --trace fixtures/trace.jsonl

# 3. 启动 HTTP 服务
./target/release/arc-cache serve --config config/example.toml
```

### HTTP 示例

```bash
# 读（未命中 -> 从存储填充）
curl -X POST localhost:18080/v1/access -H 'content-type: application/json' \
  -d '{"request_id":"r1","op":"read","page":1}'
# 写（data_hex 为十六进制负载）
curl -X POST localhost:18080/v1/access -H 'content-type: application/json' \
  -d '{"op":"write","page":3,"data_hex":"deadbeef"}'
# 动态缩容 / 扩容
curl -X POST localhost:18080/v1/resize -H 'content-type: application/json' \
  -d '{"capacity":0}'
# 观测
curl localhost:18080/v1/stats            # 计数器、p、容量
curl localhost:18080/v1/stats/history    # 采样历史
curl localhost:18080/v1/state            # 四个列表内容、脏页集合
curl 'localhost:18080/v1/diagnostics?limit=20&decision=rejected'
# 快照（先回写脏页）/ 恢复
curl -X POST localhost:18080/v1/snapshot
curl -X POST localhost:18080/v1/restore
```

## 测试组织与复核方式

- `tests/scan_pollution.rs`、`tests/hotspot_switch.rs`、
  `tests/writeback_failure.rs`、`tests/capacity_and_resize.rs`：
  **手算轨迹**，期望值（outcome 序列、p 轨迹、列表内容、各项计数器）
  在纸上按上述规则推出，不是由被测实现生成。
- `tests/parity.rs`：**独立模型逐步对照**。`tests/common/model.rs` 是与
  `src/arc.rs` 零共享代码的朴素实现；确定性伪随机轨迹（读/写/缩容 +
  确定性回写失败脚本，容量 0–7）每一步后比较：outcome、错误类别、p、
  四个列表顺序、脏页集合、全部统计计数器、后备存储内容。
- `tests/diagnostics.rs`：决策分类、请求 ID、脱敏（不断言"接口能调
  用"，断言具体记录内容与失败类别）。
- `tests/snapshot.rs`：快照前脏页必须回写、内容不入快照、缺页恢复
  失败类别。
- `tests/api_smoke.rs`：HTTP 层端到端（进程内驱动 router）。

## 已知限制

- 单写者模型：engine 由一把互斥锁保护，无并发页级控制（无 pin/引用计
  数），高并发下吞吐受锁限制。
- 页内容整体驻留内存（`Vec<u8>`），无部分页/稀疏页，无内存上限控制。
- 快照只含元数据；恢复依赖后备存储中页仍然存在（缺页即报错，不静默
  丢弃）。快照前强制回写全部脏页。
- p 的自适应使用整数步长（论文允许实数）；`|B2|/|B1|` 为整数除法。
- 回写失败不做重试/排队：访问即时报错，由调用方决定是否重试（重试
  语义已在测试中验证：状态不变，可安全重放）。
- 诊断与采样均为内存环形缓冲，进程退出即丢失（快照不含诊断历史）。
- 未实现认证/限流：HTTP 接口面向本地运维场景。
