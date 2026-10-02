# cow-snap

固定页对象空间上的写时复制（COW）快照服务。Rust + Axum + 文件系统，本地合成夹具，无外部账号依赖。

## 核心语义（固定契约）

- **页模型**：每个快照有 `logical_pages` 个逻辑页（每页 `page_size` 字节），未写过的页读作全零。物理页全局共
  享一个容量池（`capacity_pages`），跨所有快照。
- **共享页引用与独占页分离**：每个物理页有引用计数。分叉（fork）只复制页表并给每个引用 +1，不复制任何页
  内容。引用计数 = 1 的页为独占页，> 1 为共享页。
- **首次写只复制受影响页**：写共享页时，仅为该页分配新物理页并复制原内容（COW）；写独占页原地覆盖；写未
  写过的页分配新零页。一个批次只触碰它写到的页。
- **跨页写的可见性原子性**：一个写批次（`POST /snapshots/{id}/writes`）整体校验、整体规划、整体生效：
  - 输入错误、状态冲突（含引用计数异常）、容量不足都在**任何变更之前**检出，批次整体拒绝，无部分可见；
  - 批次内对同一页的多个写按顺序合并；
  - 唯一可能观察到部分批次的是提交阶段的 IO（compute）失败，且只可能影响本快照独占的页，绝不影响其他
    快照（提交顺序：先写全部新页 → 再原地写独占页 → 最后切换页表并释放旧引用 → 落盘清单）。
- **删除快照的回收**：删除只减少自己的引用；引用计数归零的页才被回收（删页文件）。其他快照因持有自己的
  引用而不受影响。
- **引用计数异常拒绝继续写**：写/删前校验快照页表引用的每个物理页计数 ≥ 1；异常时拒绝操作并返回
  `state_conflict`。`GET /diag/verify` 从全部页表重算引用计数并与记录比对，可定位异常页。

## 错误分类（四类可区分）

| category | HTTP | 含义 | 例 |
|---|---|---|---|
| `input` | 400 | 请求本身非法，重试必失败 | 页索引越界、offset+len 超页大小、空批次、base64 非法 |
| `state_conflict` | 409 | 与当前状态冲突 | 未知快照、引用计数异常 |
| `resource_exhausted` | 507 | 物理页容量耗尽 | 批次所需新页数 > 空闲页数 |
| `compute_failure` | 500 | 计算/IO 失败 | 页文件缺失/损坏、清单序列化失败、配置与清单不一致 |

错误体：`{"error": {"category", "code", "message", "run_id"}}`。`run_id` 与服务日志关联，可据此重放。

## 模块边界

| 模块 | 职责 |
|---|---|
| `src/config.rs` | 静态配置与启动校验 |
| `src/error.rs` | 四类错误分类与 HTTP 映射（所有模块共用） |
| `src/store.rs` | 资源算法：物理页分配/释放/引用计数/页文件 IO，不知快照 |
| `src/engine.rs` | 快照语义：fork、原子写批次、删除、COW 规划（运行模型：单引擎互斥串行） |
| `src/persist.rs` | 清单检查点（tmp+rename 原子重写），持久状态 |
| `src/diag.rs` | 诊断报告结构（stats / verify，只读采样状态） |
| `src/api.rs` | Axum HTTP 边界，每请求生成 run id |

## 本地启动

```bash
cargo build
COW_SNAP_DATA_DIR=./data COW_SNAP_PORT=8080 cargo run
```

环境变量：`COW_SNAP_DATA_DIR`（默认 `./data`）、`COW_SNAP_PORT`（默认 8080）、
`COW_SNAP_PAGE_SIZE`（4096）、`COW_SNAP_LOGICAL_PAGES`（64）、`COW_SNAP_CAPACITY_PAGES`（1024）。
几何参数（页大小/逻辑页数/容量）与数据目录绑定，重启时以不同几何打开同一目录会报 `compute_failure`。

## 示例请求

```bash
B=localhost:8080
# 创建基础快照
curl -s -XPOST $B/snapshots -H 'content-type: application/json' -d '{}'
# => {"run_id":"...","snapshot_id":1}

# 写两页（data_b64 为 base64 的 "hello cow-snap!"）
curl -s -XPOST $B/snapshots/1/writes -H 'content-type: application/json' -d \
  '{"writes":[{"page":0,"offset":0,"data_b64":"aGVsbG8gY293LXNuYXAh"},
              {"page":1,"offset":0,"data_b64":"aGVsbG8gY293LXNuYXAh"}]}'
# => {"pages_written":2,"cow_copies":0,"in_place_writes":0,"fresh_allocs":2,...}

# 分叉（不复制任何页）
curl -s -XPOST $B/snapshots -H 'content-type: application/json' -d '{"parent":1}'
# => {"snapshot_id":2,...}

# 子快照改写第 0 页：恰好复制 1 页
curl -s -XPOST $B/snapshots/2/writes -H 'content-type: application/json' -d \
  '{"writes":[{"page":0,"offset":0,"data_b64":"Q09XLWNvcGllZA=="}]}'
# => {"pages_written":1,"cow_copies":1,...}

# 父快照内容不变 / 诊断 / 删除父快照后子快照仍可读
curl -s $B/snapshots/1/pages/0
curl -s $B/diag/stats     # used_pages、counters(cow_copies 等)、各快照 live/shared 页数
curl -s $B/diag/verify    # 引用计数重算校验
curl -s -XDELETE $B/snapshots/1
curl -s $B/snapshots/2/pages/1
```

同目录 `examples/requests.sh` 是可执行的完整脚本版本。

## 测试

```bash
cargo test
```

覆盖场景（`tests/integration.rs`、`tests/api_http.rs`）：

1. **分叉快照**：父子各自发散写，对照独立全拷贝参考模型逐页比对；断言 COW 复制数、原地写数、实际物理页数。
2. **交叠页写**：兄弟快照写重叠页集合 + 页内部分覆盖；断言互不影响、复制页数精确。
3. **删除父快照**：删链中间与根快照，子快照内容与物理页数不变；最后删空回收全部页。
4. **容量耗尽**：所需页数 > 空闲页数时整个批次原子拒绝（无部分分配），三类错误类别可区分。
5. **批次输入错误原子性**：批次中一个非法元素使整个批次不可见。
6. **引用计数异常**：诊断钩子注入异常后，写与删均被拒绝（`state_conflict`），`verify` 定位异常页。
7. **持久化往返**：重启后快照内容、引用计数、计数器完全恢复；几何不一致拒绝打开。
8. **HTTP 边界**：状态码与错误类别对齐，错误体带 run id。

参考模型（`tests/common/mod.rs` 的 `RefModel`）是独立的朴素全拷贝实现，与被测引擎不共享代码；期望值由
测试手写常量与参考模型共同给出，不由被测核心生成。

### 测试日志

每个测试把运行编号（uuid）、关键中间状态（物理页数、计数器、异常计数）与断言理由以 JSONL 写入
`test-logs/<测试名>-<run_id>.jsonl`，仓库中保留了一次全量通过运行的日志样本。

### 最近一次运行结果

`cargo test`：10 个测试全部通过（1 个 store 单元测试 + 8 个引擎集成测试 + 1 个 HTTP 测试），0 失败。
未执行项（明确排除，见下）：崩溃中途恢复的模糊测试、并发压力测试。

## 支持范围与关键取舍

- **单进程互斥运行模型**：引擎状态由一把互斥锁串行化，无内部并发；水平扩展不在范围内。
- **整清单检查点**：每次成功变更整体重写 `manifest.json`（tmp+rename 原子替换）。页文件写与清单落盘之间
  崩溃可能留下孤儿页文件（不被任何页表引用），由运维清理；不做崩溃中途恢复模糊测试。
- **零页优化**：未写过的逻辑页不占物理容量，读作全零。
- **页内 splice 语义**：写是"从 offset 起覆盖 data 长度的字节"，不截断页。
- **parent 仅为谱系信息**：读路径完全依赖 fork 时物化的页表，删除祖先不影响后代。
- **容量语义**：容量只计物理页；fork 不耗容量，COW 与全新页各耗 1 页。
- **诊断钩子** `debug_force_refcount` 仅供测试/诊断注入异常，引擎自身不使用。
