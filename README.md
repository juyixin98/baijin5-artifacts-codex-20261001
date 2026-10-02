# mmu-teach — 受限多级页表与 TLB 地址翻译教学服务

一个**纯软件模拟**的多级页表 + TLB 地址翻译教学服务。技术栈：Rust + Axum + 文件系统（JSONL 日志 / JSON 快照）。
**不读写任何真实内核页表、不分配真实物理页、不需要任何生产账号或业务数据**——所有“物理内存”“页表”“TLB”都是进程内数据结构。

它把“一次虚拟地址如何变成物理地址、权限如何被检查、TLB 何时变陈旧”做成可观察、可重放的 HTTP 服务，适合课堂演示与夹具测试。

---

## 1. 固定的机器模型（不支持请求级修改）

| 参数 | 取值 |
|---|---|
| 页大小 | 4 KiB（`2^12`） |
| 虚拟地址 | 48 位，`bit[63:48]` 为**保留位必须为 0**，否则 `input_error` |
| 物理地址 | 40 位（PPN 28 位） |
| 页表级数 | 4 级，每级 9 位 / 512 项（类 Sv48） |
| 叶子粒度 | L1 = 1 GiB，L2 = 2 MiB，L3 = 4 KiB；**L0 禁止叶子** |
| PTE 位 | `V R W X U G`；`W=1,R=0` 非法；大页 PPN 低地址位必须 0；`bit[63:54]` 保留必须 0 |
| ASID | 8 位（0..=255），最多 64 个地址空间 |
| TLB | 全相联 LRU，容量 16，键为 `(ASID, 4KiB VPN)`，另有全局 `G=1` 表项 |
| 单次访存 | 最多 16 字节，可跨 1 个 4KiB 边界 |

页跨越访问会被**拆成同页片段，对每一片独立完成翻译与 R/W/X 全部权限检查**；任一片失败，整次访存按页故障报告，并保留此前成功片段的中间状态。

### 页故障类别（输出故障，**不 panic**）

| `fault.kind` | 含义 |
|---|---|
| `miss` | 遍历中途（非末级）遇到 `V=0`，下一级页表不存在 |
| `page_not_present` | 末级 `V=0`，页未映射 |
| `permission_denied` | 叶子存在但缺少请求的 R/W/X 权限 |
| `reserved_fault` | 保留位 / 非法权限组合 / 大页 PPN 未对齐 / L0 叶子等 |

### 服务错误四分类（与页故障区分）

| `kind` | HTTP | 场景 |
|---|---|---|
| `input_error` | 400 | 非规范地址、未对齐、非法 level、JSON 无法解析 |
| `conflict` | 409 | ASID 不存在、**大小页覆盖冲突**、重复/错误粒度 unmap |
| `resource_exhausted` | 507 | 物理帧池耗尽、地址空间数触顶 |
| `compute_failure` | 500 | 内部不一致（如双重回收、快照状态损坏） |

> 页故障是**正常模拟结果**：`POST /api/v1/translate|access` 对页故障返回 **HTTP 200** + 判别式 JSON（`"result":"page_fault"`），故障以稳定字符串给出。

---

## 2. 关键行为契约

1. **固定参数 + 跨页拆分验证全部权限**：见上表；`access` 返回每片的物理地址、来源（TLB / 遍历）与故障类别。
2. **明确的 TLB 失效协议 + ASID 隔离**：
   - `map` / `unmap` / `reprotect` 成功后**自动**按受影响叶子覆盖的 4KiB VPN 范围逐页刷除（`reprotect` 覆盖整个大页，防止降级后陈旧权限）；
   - `destroy_process` 执行 ASID 局部 SFENCE；`G=1` 全局项跨 ASID 可见且不被普通 ASID 刷除；
   - 另有显式 `POST /api/v1/sfence`（页 / ASID / 全刷三档）。
3. **大页与小页覆盖冲突拒绝**：两个方向都返回 `conflict`——先大页后在内部建小页、先小页后建覆盖大页，均拒绝。
4. **输出页故障类型而非 panic**：全部故障建模为数据（`PageFault { kind, vaddr, asid, level, index, pte, reason }`）。
5. **TLB 过期教学夹具**：`POST /raw-pte` 直接改写路径上某级 PTE 的原始值且**故意不刷 TLB**，用于复现“陈旧表项仍放行”，再用 `sfence` 恢复正确行为。该接口在响应中带 `warning`。

---

## 3. 本地启动

需要 Rust（开发环境为 1.98）。无外部服务、无密钥。

```bash
cargo run --release                 # 默认监听 127.0.0.1:8080，帧池 4096
# 可选环境变量
MMU_LISTEN=127.0.0.1:8080 \
MMU_FRAMES=8192 \
MMU_RUNLOG=./runs.jsonl \
  cargo run --release
```

- `MMU_FRAMES`：模拟物理帧数（每帧 4 KiB，页表也从该池分配）。
- `MMU_RUNLOG`：设置后每次操作以 JSONL 追加落盘（运行编号 / 输入 / 中间状态 / 理由），用于离线重放；不设则只保留内存环形缓冲（1000 条）。

依赖版本已在 `Cargo.lock` 锁定（axum 0.8.9 / tokio 1.53.1 / serde 1.0.229 / serde_json 1.0.151）。

---

## 4. 示例请求（节选，完整集合见 `examples/requests.sh`）

```bash
B=http://127.0.0.1:8080; CT="-H content-type:application/json"

# 建两个进程（返回 ASID）
curl $CT -XPOST $B/api/v1/processes -d '{"name":"A"}'
curl $CT -XPOST $B/api/v1/processes -d '{"name":"B"}'

# 同一虚址 0x4000 映射到不同物理帧
curl $CT -XPOST $B/api/v1/processes/0/mappings \
  -d '{"vaddr":"0x4000","level":3,"ppn":100,"read":true,"write":true,"execute":true,"user":true}'
curl $CT -XPOST $B/api/v1/processes/1/mappings \
  -d '{"vaddr":"0x4000","level":3,"ppn":200,"read":true,"write":true,"execute":true,"user":true}'

# 翻译：A -> 100*4096+0x10 = 409616；第二次命中 TLB
curl $CT -XPOST $B/api/v1/translate -d '{"asid":0,"vaddr":"0x4010","op":"read"}'

# 跨 4KiB 边界写（0x0ff8 起 16 字节），两片各自翻译+验权
curl $CT -XPOST $B/api/v1/access   -d '{"asid":0,"vaddr":"0x0ff8","len":16,"op":"write"}'

# 建 2MiB 大页（PPN 须 512 对齐）；翻译大页内偏移
curl $CT -XPOST $B/api/v1/processes/1/mappings \
  -d '{"vaddr":"0x200000","level":2,"ppn":512,"read":true,"write":true,"execute":true,"user":true}'
curl $CT -XPOST $B/api/v1/translate -d '{"asid":1,"vaddr":"0x200abc","op":"read"}'

# 权限降级（自动刷 TLB），随后写 -> 200 + page_fault/permission_denied
curl $CT -XPOST $B/api/v1/processes/0/protect -d '{"vaddr":"0x4000","read":true,"execute":true,"user":true}'
curl $CT -XPOST $B/api/v1/translate -d '{"asid":0,"vaddr":"0x4000","op":"write"}'

# 诊断与持久化
curl "$B/api/v1/runs?limit=20"                 # 运行编号 + 中间状态 + 判断理由
curl "$B/api/v1/runs/12"                       # 按 run_id 取回（重放）
curl $CT -XPOST $B/api/v1/snapshot/save -d '{"path":"./snap.json"}'
curl $CT -XPOST $B/api/v1/snapshot/load -d '{"path":"./snap.json"}'
```

数字字段接受 JSON 数字或字符串，支持 `0x..` / `0o..` / `0b..` 与下划线分隔（如 `"0xdead_0000"`）。

### 端点一览

`GET /` · `GET /api/v1/info` · `GET|POST /api/v1/processes` · `DELETE /api/v1/processes/{asid}`
· `POST .../mappings` · `POST .../mappings/unmap` · `POST .../protect` · `POST .../raw-pte`
· `GET .../walk?vaddr=` · `POST /api/v1/translate` · `POST /api/v1/access` · `POST /api/v1/sfence`
· `GET /api/v1/tlb` · `GET /api/v1/runs` · `GET /api/v1/runs/{id}`
· `GET /api/v1/snapshot` · `POST /api/v1/snapshot/save|load`

---

## 5. 工程组织（模块边界与数据/错误契约）

```
src/
  config.rs     固定机器参数与容量
  errors.rs     FaultKind / PageFault / ServiceError 四类错误契约
  address.rs    虚/实地址分解、规范校验、跨页拆分（纯函数）
  pte.rs        PTE 编解码、权限、保留位校验（纯函数）
  memory.rs     物理帧池：最低空闲帧分配、按帧释放、耗尽分类
  tlb.rs        ASID 标签全相联 LRU、三级失效协议
  pagetable.rs  多级页表树：遍历、映射、冲突拒绝、级联回收（核心）
  mmu.rs        翻译 + 权限 + 跨页访存执行层
  machine.rs    多地址空间编排、自动失效、快照持久化
  runlog.rs     运行编号环形日志 + JSONL 落盘
  api.rs        Axum 诊断/操作接口与状态码契约
  main.rs       入口与环境变量
tests/
  common/mod.rs 独立参考预言机 Oracle（区间地面真值，不复用被测遍历）
  contracts.rs  行为契约夹具（9 项）
  api.rs        HTTP 端到端夹具（5 项）
```

- **运行模型**：`pagetable` + `mmu`；**资源算法**：`address`/`pte`/`memory`；
  **持久或采样状态**：`runlog`（JSONL）+ `machine` 快照（JSON）；**诊断接口**：`api` + `walk`/`tlb`/`runs`。
- 模块间只通过显式类型传递：页故障走 `PageFault`，服务错误走 `ServiceError` 四分类，翻译层用 `MachineTranslateError { Input, Fault }` 区分“输入不合法”与“合法访问触发故障”。
- 所有源文件均低于 800 行软上限（最大 `machine.rs`）。

---

## 6. 测试与验证

```bash
cargo test                                   # 35 个测试（17 单元 + 5 HTTP + 13 契约）
cargo clippy --all-targets -- -D warnings    # 零警告
```

夹具（`tests/contracts.rs`，13 项）覆盖要求的全部场景，且**断言具体结果与失败类别**，不是“接口能调用”：

- 同虚址不同进程 → 物理地址不同且 TLB 命中不串号（ASID 隔离）；
- 跨页写成功拆分两片；第二页权限降级后跨页写在第二片 `permission_denied`；
- 权限降级经自动失效后立即生效；
- **TLB 过期**：`raw-pte` 不刷 → 陈旧 `tlb_hit` 仍放行 → `sfence` 后拒绝；
- 大页/小页覆盖两个方向均 `conflict`；
- `miss` / `page_not_present` / `permission_denied` / `reserved_fault` 与非规范地址 `input_error` 分类；
- 大页物理地址、帧池 `resource_exhausted`。

**参考答案不由被测核心自身生成**：`tests/common/mod.rs` 的 `Oracle` 是独立的区间映射实现，依据测试声明的 `(va, size, ppn)` 地面真值复算期望物理地址；部分常量（索引、PPN）为手算并在测试旁注推导。

真实运行记录保存在 `test-results/`（含时间戳、`cargo test` 全量输出与 clippy 结果）。

### 支持范围与关键取舍

- 教学取向：**不模拟缓存层级、TLB 填充延迟、硬件页表 walker 微架构、并发竞态**；翻译是同步确定性函数。
- 物理内存是**编号集合**而非可读写字节数组：服务回答“翻译成哪个物理帧/地址、权限是否允许”，不在模拟内存里存放数据值。
- TLB 以 4KiB 粒度缓存（大页翻译在填入时物化为基页项），这简化了键设计与失效范围。
- 快照保存稀疏页表（PPN→512 PTE），不保存运行日志与 TLB（恢复后 TLB 为空，语义上等同一次全局 SFENCE）。
- 帧分配确定性地取最低空闲 PPN，便于夹具复算；不是伙伴分配器。
