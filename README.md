# cow-snapshot-service

固定页对象空间上的**写时复制（CoW）快照服务**。Rust + Axum + 文件系统，无外部服务依赖。

核心语义：逻辑卷是 `page_count` 个固定大小（`page_size` 字节）的页；页内容存放在页对象存储中，
多个映射（live 卷 + 各快照）通过**引用计数**共享同一个页对象。首次写只复制受影响的共享页，
独占页原地写入。

## 核心契约

1. **共享页引用与独占页分离，首次写只复制受影响页**
   - 写请求按页判定：对象 `refcount > 1`（被快照或其他映射共享）→ 复制出新对象后写入；
     `refcount == 1`（live 独占）→ 原地覆盖，不分配新对象。
   - 每个批次的 `BatchReport` 返回 `pages_copied` / `pages_in_place`，可直接验证复制页数。
2. **跨页写对快照可见性原子（显式批次）**
   - 一次 `POST /live/writes` 就是一个原子批次：先完成全部输入校验与容量预检，再在引擎锁内
     执行并一次性切换 live 映射；失败时按 undo 日志回滚。观察者只能看到批次前或批次后的状态，
     不会看到批次的中间态。同批次内同页多次写按提交顺序生效（后者覆盖前者）。
   - 崩溃原子性为尽力而为：元数据 tmp+rename 写入，但**没有 WAL**（见"关键取舍"）。
3. **删除快照的回收不影响其他快照**
   - 删除只递减该快照映射中各对象的引用计数；计数归零才释放对象文件。其他快照与 live 卷
     持有的引用使对象存活，内容不受任何影响。
4. **页引用计数异常拒绝继续写**
   - 每次变更前后校验不变量：`sum(refcounts) == page_count × (1 + 快照数)`，并逐对象检查
     （未知对象、计数下溢、共享对象原地写、对象文件缺失）。
   - 任何 `integrity` 类失败使服务进入**隔离（quarantine）**：此后一切变更（写/分叉/删除）
     返回 `state_conflict/quarantined`，读取与诊断仍可用。运维修复元数据并通过
     `POST /diag/quarantine/clear`（内部会先跑一次审计，审计不干净则拒绝清除）后恢复。

## 运行模型

- 单进程、tokio 多线程运行时；所有引擎变更经一把 `std::sync::Mutex` 串行化（单写者），
  读操作同锁保证一致性视图。规模定位为本地/测试服务，不做并发写优化。
- 每次提交后的变更都把元数据落盘（tmp+rename），重启后从数据目录恢复
  （`persistence_restart` 测试覆盖）。

### 数据目录布局

```
<data_dir>/
  pages/<obj_id>.page      页对象内容（恰好 page_size 字节）
  meta/store.json          引用计数表 { page_size, next_id, objects: {id: refcount} }
  meta/engine.json         live 映射、快照映射、统计、隔离状态
  log/events-<run_id>.jsonl  结构化事件日志（每次运行一个文件）
```

### 事件日志与可重放性

每个变更操作记录一条 JSON 事件：运行编号 `run_id`（`run-<时间>-p<pid>-n<纳秒>`）、单调序号、
操作名、结果、失败时的错误类别与原因、关键中间状态（对象数、总引用数、每页 copy/in-place 判定）。
`GET /diag/events?limit=N` 读取内存中的最近事件；完整历史在 `log/events-<run_id>.jsonl`。

## 模块划分与契约

| 模块 | 职责 | 对外契约 |
|---|---|---|
| `error.rs` | 错误分类 | `ServiceError{category, code, message}`，五类（下表） |
| `config.rs` | 配置 | 环境变量 / 直接构造；启动时校验 |
| `eventlog.rs` | 运行编号 + JSONL 事件日志 | `record_ok/record_err/recent` |
| `page_store.rs` | 页对象文件 + 引用计数表 + 容量 | 变更不自动落盘，`persist()` 为提交点；异常一律 `integrity` |
| `engine.rs` | CoW 语义：批写/分叉/删除/审计/隔离 | 全部语义所在层；HTTP 层不含逻辑 |
| `api.rs` | Axum 路由、DTO、base64、HTTP 映射 | 错误体 `{error:{category,code,message}}` |
| `main.rs` | 启动：配置 → 引擎 → HTTP | 环境变量见下 |

### 错误分类（可区分的失败类别）

| category | 含义 | HTTP | 典型 code |
|---|---|---|---|
| `input` | 请求输入非法 | 400 | `page_out_of_range` `write_out_of_bounds` `empty_write` `empty_batch` `batch_too_large` `bad_base64` |
| `state_conflict` | 与当前状态冲突 | 409 | `snapshot_not_found` `snapshot_exists` `quarantined` `audit_still_failing` `config_mismatch` |
| `resource_exhausted` | 页对象容量耗尽 | 507 | `capacity_exhausted` |
| `integrity` | 引用计数/元数据异常 | 500 | `refcount_anomaly` `object_file_missing` `meta_corrupt` |
| `internal` | IO 等内部失败 | 500 | `io` |

## 本地启动

依赖锁定在 `Cargo.lock`（构建：`cargo build --locked`）。

```bash
cargo build
COW_DATA_DIR=./data \
COW_PAGE_SIZE=4096 \      # 页大小（字节），数据目录生命周期内固定
COW_PAGE_COUNT=256 \      # 逻辑卷页数，固定
COW_CAPACITY=4096 \       # 页对象容量上限（共享+独占对象总数）
COW_MAX_BATCH_WRITES=1024 \
COW_BIND=127.0.0.1:8080 \
./target/debug/cow-snapshot-service
```

已有数据目录会按存储的 `page_size`/`page_count` 校验，不匹配拒绝启动（`state_conflict/config_mismatch`）。

## 示例请求

```bash
# 写一页（数据为 base64；一个请求即一个原子批次，可含多页）
curl -X POST localhost:8080/live/writes -H 'content-type: application/json' \
  -d '{"writes":[{"page":3,"offset":0,"data_b64":"aGVsbG8="}]}'
# => {"batch_seq":1,"pages_touched":1,"pages_copied":1,"pages_in_place":0,"objects_used":2,"capacity":4096}

# 分叉快照（不复制任何页对象）
curl -X POST localhost:8080/snapshots -H 'content-type: application/json' -d '{"name":"baseline"}'

# 再写同一页：共享对象被复制，快照内容不变
curl -X POST localhost:8080/live/writes -H 'content-type: application/json' \
  -d '{"writes":[{"page":3,"offset":0,"data_b64":"V09STEQ="}]}'
curl localhost:8080/snapshots/1/pages/3   # 仍是 "hello..."
curl localhost:8080/live/pages/3          # 已是 "WORLD..."

# 诊断：统计 / 审计 / 事件 / 解除隔离
curl localhost:8080/diag/stats
curl -X POST localhost:8080/diag/audit -H 'content-type: application/json' -d '{"enforce":true}'
curl 'localhost:8080/diag/events?limit=20'
curl -X POST localhost:8080/diag/quarantine/clear

# 删除快照（只回收无引用的对象）
curl -X DELETE localhost:8080/snapshots/1
```

## 测试

```bash
./scripts/run-tests.sh    # 或 cargo test -- --nocapture
```

- 测试日志（含各用例的 run_id、关键中间状态、判定理由）保存在 `test-results/last-run.txt`
  及 `test-results/run-<时间戳>.txt`；每个用例的引擎事件 JSONL 在
  `target/cow-test-data/<用例名>/log/`（可由 run_id 关联重放）。
- 参考实现 `tests/common/mod.rs` 的 `FullCopyRef` 是**独立的全拷贝模型**（纯 std、深拷贝），
  与被测引擎不共享任何代码；每个用例驱动两边执行同一操作序列并逐页比对。
  期望的复制页数/对象数在用例内以注释推导、硬编码断言，不取自被测实现。

用例清单（`tests/cow_contract.rs`、`tests/http_api.rs`）：

| 用例 | 验证点 |
|---|---|
| `fork_then_write_copies_only_touched_pages` | 分叉后写只复制受影响共享页（复制数=3，对象数 4→7） |
| `exclusive_page_write_is_in_place` | 独占页原地写，复制数=0，对象数不变 |
| `overlapping_writes_across_snapshots` | 两个快照各自看到不同版本；删除其一不影响另一个 |
| `delete_parent_snapshot_reclaims_only_unreferenced` | 删父快照零回收；删子快照精确回收 4 个无引用对象 |
| `capacity_exhaustion_is_atomic` | 容量不足整批拒绝（`resource_exhausted`），无部分写入，服务可继续 |
| `refcount_anomaly_quarantines_writes` | 注入计数损坏→写被拒（`integrity`）→隔离→修复前禁止清除→恢复 |
| `input_validation_and_state_conflicts` | 输入错误与状态冲突分类断言，且失败不污染后续操作 |
| `persistence_restart` | 重启后内容/快照/统计恢复，run_id 更新 |
| `randomized_cross_check_against_full_copy` | 200 个确定性随机操作逐步对照全拷贝参考 + 不变量 |
| `http_end_to_end` / `http_error_categories` | HTTP 层状态码、错误体契约、base64 处理 |

最近一次运行结果见 `test-results/last-run.txt`（当前：11 通过 / 0 失败 / 0 未执行）。

## 支持范围与关键取舍

**支持**：单机本地服务；固定页空间；原子多页批写；快照分叉/删除/读取；容量管理；
引用计数审计与隔离；崩溃后从数据目录恢复；结构化事件日志。

**取舍与限制**：

- **无 WAL/日志前导**：元数据 tmp+rename 保证单文件不半写，但"对象文件已写、元数据未提交"
  的崩溃窗口会产生孤儿对象文件。孤儿在审计中报告（`orphan_files`）但不视为损坏、不自动删除；
  引用计数与映射不一致时重启后服务以隔离状态启动，等待运维处理。
- **单写者互斥**：不为并发写做优化；批次在锁内完成，这是原子性契约的实现基础。
- **容量模型**：容量按"页对象个数"计（共享对象只占一份），不按字节；`capacity` 是运行策略，
  重启时可调整，`page_size`/`page_count` 则不可变。
- **同批同对象保守复制**：同一批次写两个指向同一共享对象的页时各复制一份（不多做"后者接管
  旧对象"的优化），语义正确优先。
- **传输编码**：页内容走 base64 JSON，约 33% 开销；定位为控制面/测试服务而非高吞吐数据面。
- **审计为按需触发**：写路径只做 O(对象数) 的总计数不变量与逐对象检查，完整逐对象对账由
  `POST /diag/audit`（或启动时）执行。
