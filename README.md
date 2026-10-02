# mmu-lab — 受限多级页表与 TLB 地址翻译教学服务

一个只在**本地合成环境**中运行的教学服务：模拟一台参数固定的 32 位机器的
二级页表（类 RISC-V Sv32 的 10/10/12 切分）、4 MiB 大页、容量受限的 TLB，
以及显式 TLB 失效协议。

> **安全边界**：本服务读写的“物理内存”“页表”全部是进程内数据结构和本地
> JSON 快照，**不打开 /dev/mem、不 mmap 真实页表、不做任何特权操作**。
> 所有参与者都是本地合成夹具，无需任何生产账号或真实业务数据。

技术栈：Rust + [Axum](https://github.com/tokio-rs/axum) 0.8 + Tokio +
serde/serde_json；持久化为本地文件（临时文件 + fsync + rename 原子替换）。

---

## 1. 支持范围与固定机器参数

| 参数 | 值 | 是否可改 |
|---|---|---|
| 虚拟地址位数 | 32 | 固定（行为契约） |
| 物理地址位数 | 32 | 固定 |
| 基页大小 | 4 KiB | 固定 |
| 大页大小 | 4 MiB（1024 个基页） | 固定 |
| 页表级数 | 2（L1/L2，索引各 10 位） | 固定 |
| VA 切分 | L1[31:22] / L2[21:12] / offset[11:0] | 固定 |
| 保留位 | PTE bits 9:7、39:32、63:40 必须为 0 | 固定 |
| 最大访问长度 | 4 MiB（一个大页） | 固定 |
| 物理帧池帧数 | 默认 65 536（256 MiB） | 可配置 |
| TLB 容量 | 默认 16 | 可配置 |
| ASID 上界 | 默认 255 | 可配置 |

PTE 概念位布局（64 位表项，便于讲保留位；PPN 为 20 位）：

```text
 63  40| 39 32| 31       12|11 10| 9 7| 6  5 4 3 2 1 0
保留(0) |保留(0)|   PPN[19:0] |  0  |保留0|PS G D X W R V
```

- L1 叶子必须 `PS=1`（4 MiB 大页），L1 `PS=0` 但 R/W/X≠0 属保留编码；
- L2（末级）出现 `PS=1` 或“继续指向下级”的分支编码都是故障；
- 叶子必须至少授予 R/W/X 一种权限（全 0 是分支 PTE 编码）。

### 行为契约如何落实

1. **固定参数 + 跨页拆分逐段验权**：`config.rs` 常量即契约，不可经 API 修改；
   `store/translate.rs` 把访问区间按**实际命中的页大小**拆成多段，逐段检查
   fetch/read/write 权限，任一段故障则整次翻译返回类型化故障，故障地址精确
   到该段首址，并保留故障前已完成的段。
2. **显式失效协议 + ASID 隔离**：`map/unmap/protect` 默认按页大小精确失效
   TLB，但允许 `invalidate:false` **刻意保留**旧条目构造“TLB 过期”夹具——
   模型绝不会偷偷刷新。TLB 条目带 ASID，非全局条目只被同 ASID 命中；
   `G=1` 的全局条目任何 ASID 命中，失效时跨 ASID 清除。
3. **大页/小页覆盖冲突拒绝**：建映射时对覆盖范围做相交检测，大页包裹小页、
   小页钻入大页、同 VA 重复映射分别返回不同的状态冲突码。
4. **输出故障类型而非 panic**：翻译“成功地判定故障”，返回 200 +
   `outcome=page_fault`；工程错误才是非 2xx。

### 明确的取舍（不支持什么）

- 不模拟数据内容（不 load/store 字节），只翻译地址与权限；
- 不模拟 A 位（访问位）自动置位；D 位在建映射/提权时按写权限设置，仅作展示；
- TLB 为**单条全相联 FIFO**（教学可预测），不是组相联 LRU；
- 物理内存是帧编号集合，不模拟总线、缓存别名或着色；
- 无鉴权/多租户：这是本地单机教学服务，默认只监听 127.0.0.1；
- 大页的物理帧从同一帧池按 4 MiB 对齐分配，不区分“大页专用内存”。

---

## 2. 工程组织（模块边界与数据/错误契约）

```text
src/
├── config.rs          固定机器参数、Config 资源参数、VA 切分（纯函数）
├── types.rs           跨模块数据契约：Permissions/Pte/请求/结果/Segment
├── error.rs           错误契约：DomainError 五类 + FaultKind 六种页故障
├── frames.rs          资源算法：物理帧池（对齐连续分配、预留、回收、耗尽）
├── tlb.rs             TLB：ASID 隔离、全局位、FIFO、显式失效、过期快照
├── mmu.rs             页表内存与纯走表 walk()（不依赖 TLB/网络）
├── events.rs          诊断事件环：run_id、理由、中间状态、失败类别
├── store/
│   ├── mod.rs         中央状态机 Lab：生命周期、记账、快照/恢复
│   ├── mapping.rs     map/unmap/protect、覆盖冲突、全局分发、失效协议
│   └── translate.rs   TLB 快路径 + 走表慢路径 + 跨页逐段验证
├── persistence.rs     文件原子快照（JSON）
├── api/mod.rs         Axum 诊断接口与统一响应信封
└── main.rs            启动入口（环境变量配置、优雅退出）
tests/
├── common/mod.rs      独立参考模型 RefModel（BTreeMap 直接查）+ HTTP 助手
├── core_contract.rs   13 个核心契约测试（四类夹具 + 资源/错误分类）
└── http_contract.rs   7 个接口契约测试（信封/状态码/run_id/快照往返）
```

### 页故障分类（HTTP 200，`data.fault.kind.type`）

| 类型 | code | 含义 |
|---|---|---|
| `not_present` | `PAGE_FAULT_NOT_PRESENT` | 某级 PTE V=0（带 level/index） |
| `permission_denied` | `PAGE_FAULT_PERMISSION` | 叶子存在但不满足本次 fetch/read/write |
| `reserved_bit` | `PAGE_FAULT_RESERVED_BIT` | PTE 保留位非零 |
| `misconfigured_page_size` | `PAGE_FAULT_BAD_PS` | PS 位出现在非法级别 |
| `reserved_encoding` | `PAGE_FAULT_RESERVED_ENCODING` | 该级 PTE 编码无意义（如末级分支项） |
| `misaligned_next_table` / `misaligned_leaf` | `..._ALIGN` | 次表/叶子物理基址对齐错误 |

### 工程错误分类（非 2xx，信封 `category` + `code`）

| category | HTTP | 典型 code |
|---|---|---|
| `input_error` | 400 | `INVALID_REQUEST` / `INVALID_JSON` |
| `state_conflict` | 409 | `UNKNOWN_ASID` / `MAPPING_EXISTS` / `LARGE_SMALL_OVERLAP` / `PHYSICAL_RANGE_OCCUPIED` |
| `resource_exhausted` | 507 | `OUT_OF_FRAMES`（带 requested/available）/ `OUT_OF_ASIDS` |
| `computation_failure` | 422 | `COMPUTATION_FAILED`（溢出、记账不一致） |
| `not_found` | 404 | `NOT_FOUND`（资源 / 过期 run_id） |

---

## 3. 本地启动

需要 Rust 1.75+（开发使用 1.98）。依赖已锁定，见 `Cargo.lock`。

```bash
cargo run --release                 # 默认监听 127.0.0.1:8080
MMU_LISTEN=127.0.0.1:9090 \
MMU_TOTAL_FRAMES=4096 MMU_TLB_CAPACITY=8 \
cargo run
MMU_AUTOLOAD=1 cargo run            # 启动时自动载入 ./store/mmu-lab.json
```

环境变量：`MMU_LISTEN`、`MMU_TOTAL_FRAMES`、`MMU_TLB_CAPACITY`、
`MMU_MAX_ASIDS`、`MMU_EVENT_BUFFER`、`MMU_SNAPSHOT_PATH`、`MMU_AUTOLOAD`。

快速验证：

```bash
curl -s localhost:8080/health
curl -s localhost:8080/machine
bash examples/requests.sh           # 完整示例序列（建议安装 jq 美化输出）
```

---

## 4. 示例请求

```bash
# 建地址空间
curl -s -XPOST localhost:8080/asids -d '{"name":"proc-a"}'

# 建 4K 映射（pa 省略 -> 帧池确定性分配）
curl -s -XPOST localhost:8080/maps -H 'content-type: application/json' -d '{
  "asid":1,"va":4096,"page":"4K",
  "permissions":{"read":true,"write":true,"execute":true}}'

# 翻译（跨页写示例：0x1ff0 起 32 字节）
curl -s -XPOST localhost:8080/translate -H 'content-type: application/json' -d '{
  "asid":1,"va":8176,"access":"write","len":32}'

# 权限降级但不失效（TLB 过期夹具）
curl -s -XPOST localhost:8080/maps/protect -H 'content-type: application/json' -d '{
  "asid":1,"va":4096,
  "permissions":{"read":true,"write":false,"execute":true},
  "invalidate":false}'

# 显式失效（VA 级 / ASID 级 / 全局冲刷）
curl -s -XPOST localhost:8080/tlb/invalidate -d '{"asid":1,"va":4096,"page":"4K"}'
curl -s -XPOST localhost:8080/tlb/invalidate -d '{"asid":1}'
curl -s -XPOST localhost:8080/tlb/invalidate -d '{}'

# 快照与诊断
curl -s -XPOST localhost:8080/snapshot/save -d '{"path":"./store/demo.json"}'
curl -s 'localhost:8080/events?limit=10'
curl -s localhost:8080/events/<run_id>
```

翻译成功负载里的关键字段：`pa`、`page`、`tlb_hit`、`tlb_segments`、
`walk_segments`、`stale_segments`、`evictions`、`walk_levels`、`segments[]`
（每段含 `source: tlb|walk`、`stale`、权限位）。

每个响应与 `GET /events` 中的事件共享同一个 **`run_id`**（UTC 时间戳 + 单调
序号，如 `20261002T073015Z-000042`）；事件内含 `rationale`（判断理由）与
`state`（走表轨迹、拆分段等关键中间状态），可据此重放问题。

---

## 5. 测试与验证材料

```bash
cargo test            # 23 单元 + 13 核心契约 + 7 HTTP 契约 = 43 个测试
cargo clippy --all-targets   # 零警告
```

**独立参考实现**：`tests/common/mod.rs` 的 `RefModel` 用与被测核心完全不同的
代码路径（BTreeMap 按对齐基址直接查找、大页优先）独立计算期望物理地址、权限
判定和跨页拆分段；集成测试把被测结果与 `RefModel` 的结果逐段比对——期望答案
不是由被测核心自己生成的。

四类规定夹具的落点：

| 夹具 | 核心测试 | HTTP 测试 |
|---|---|---|
| 同虚址不同进程 | `same_va_in_two_processes_...`、`unmapped_va_in_other_process_...` | `end_to_end_translation_and_fault_envelope` |
| 跨页写逐段验权 | `cross_page_write_split_...`、`cross_page_access_into_unmapped_...` | `examples/requests.sh` 第 4 步 |
| 权限降级 + TLB 过期 | `permission_downgrade_without_invalidation_...`、`tlb_capacity_evicts_fifo_...` | `overlap_conflict_and_tlb_staleness_over_http` |
| 大页/小页覆盖冲突 | `large_small_overlap_is_rejected_in_both_directions`、`duplicate_mapping_...` | `overlap_conflict_and_tlb_staleness_over_http` |

测试只断言**具体结果与失败类别**（精确 PA、段数、故障 `type`、故障地址、
HTTP 状态码、`category/code`、淘汰原因与被淘汰标签等），不断言“接口可调”。

测试运行记录保存在 `test-results/`（含开发过程中出现过的失败与最终全绿记录）。

---

## 6. 持久化

- `POST /snapshot/save`（body 可给 `path`，否则用 `MMU_SNAPSHOT_PATH`）：
  帧池、全部页表内容、TLB（含装入序号）、地址空间元数据、映射记账与配置一起
  序列化为版本化 JSON，经“临时文件 → fsync → rename”原子落盘；
- `POST /snapshot/load`：读入并校验版本/配置后整体替换内存状态；
- 诊断事件环不进快照（它是进程内观察设施）。
