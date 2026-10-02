# 边界语义（Boundary Semantics）

本文档定义 mmap-model 的精确行为边界。模型在内存中复刻 Linux
`mmap`/`msync`/`ftruncate`/`munmap` 的关键语义，**不修改系统内核**，
所有状态保存在进程内（页缓存）与可声明的本地持久存储（`FsStore` 目录 /
`MemStore`）中。

## 术语

- **页缓存（page cache）**：每个文件一份，共享映射直接读写它。
- **私有页（COW page）**：私有映射首次写某页时从页缓存复制出的副本，按映射持有。
- **持久镜像（persistent image）**：`BackingStore` 中的字节序列，只有 `sync`
  成功或 `write_file_direct`（外部写入者夹具）才改变它。
- **EOF**：文件的逻辑大小，由 `create_file`/`truncate` 决定。

## 访问窗口

- 映射偏移必须页对齐，长度必须 > 0，否则 `invalid_argument`。
- 读写窗口必须完全落在映射长度内，否则 `invalid_argument`。
- 零长读写是 `invalid_argument`。

## EOF 与非法访问（核心边界）

设页大小为 P，EOF 为 S：

| 区域 | 读 | 写 |
|------|----|----|
| 页完全在 EOF 内（`page_start + P <= S`） | 正常 | 正常 |
| 最后一个部分页（`page_start < S < page_start + P`） | 有效部分返回数据；**超出 EOF 的尾部按页缓存内容返回**（见下） | 允许写入页缓存，但 `sync` 只回写 `[page_start, S)` |
| 页完全超出 EOF（`page_start >= S`） | **`access_out_of_range`（SIGBUS 类比），绝不返回伪造的零页** | 同左 |

关键规则：**非法访问（整页超出 EOF）永远以错误类别
`access_out_of_range` 失败，模型不会用零填充伪装成成功读取。**
零只出现在两种合法情形：(1) 稀疏存储本身读出的零（如新建文件、
`truncate` 扩大的区域）；(2) `truncate` 收缩时显式清零的部分页尾部。

### 部分页尾部的可见性（与 Linux 页缓存行为一致）

向部分页超出 EOF 的尾部写入的数据保留在页缓存页中：

- `sync` 不回写它（只写 `[page_start, EOF)`）；
- 之后 `truncate` **扩大**文件使该区域落入 EOF 内时，之前写入的数据
  变为可见（测试 `cross_page_truncate` 第 6 步验证）；
- `truncate` **收缩**经过该页时，尾部被显式清零。

## 共享映射（shared）

- 写直接进入页缓存并置脏标记，**同一文件的其他共享映射立即可见**。
- `sync` 把映射范围内所有脏页回写到持久存储并清除脏标记，然后调用
  `flush`（fsync 类比）。
- 解除映射不清除页缓存：脏页仍驻留，之后的映射仍可读到并同步它。

## 私有映射（private）

- 读：优先读自己的 COW 页，否则读共享页缓存（因此能看到共享映射
  在自己 COW 该页**之前**写入的内容）。
- 写：首次写某页触发 COW——复制当前共享内容为本映射私有页，之后
  该页的读写都走私有副本；**页缓存不被触碰，不产生脏页**。
- `sync`：私有页永不回写，返回 `skipped_private` 列表；不接触存储，
  因此存储故障不会使私有 `sync` 失败。
- 解除映射：私有页随映射销毁。

## 同步失败

- 逐页回写：成功的页清脏，**失败的页保留脏标记**，错误类别
  `sync_failed`，`failed_pages` 列出失败页。
- `flush` 失败同样使整次 `sync` 以 `sync_failed` 失败（此时页可能已
  写入存储但持久性未确认）。
- 重试 `sync` 只处理仍脏的页。

## 截断（truncate）

- **收缩**：页起始地址 >= 新 EOF 的缓存页被丢弃（无论脏否，报告
  `discarded_pages` 及各自的 `was_dirty`）；新的最后部分页超出 EOF
  的尾部被清零（报告 `zeroed_tail_page`）。
- **扩大**：只移动 EOF；新可及区域按需从稀疏存储读入，读为零。
- 截断到 0 后任何访问都是 `access_out_of_range`。

## 解除映射（unmap）

- 共享：报告仍驻留缓存的脏页（`dirty_pages_left_in_cache`），数据不丢。
- 私有：报告被丢弃的 COW 页（`discarded_private_pages`）。

## 外部写入者（`write_file_direct` / `POST /files/write`）

直接写持久存储、绕过页缓存，用于构造夹具和模拟外部写入者。
**有意不与页缓存一致**：若同一页已在缓存中，映射读到的是缓存内容。

## 未执行 / 无法在本环境执行的检查（单列，不视为已通过）

1. 真实内核 SIGBUS 信号投递（本模型以错误类别表达，不投递信号）。
2. 多线程并发访问同一映射的 TLB/缺页竞态语义（模型为单锁串行化）。
3. 页回收 / 内存压力下的淘汰策略（模型不淘汰缓存页）。
4. `msync` 的 `MS_ASYNC`/`MS_INVALIDATE` 变体（模型只实现同步回写）。
5. 文件锁（`flock`/`fcntl`）与 mmap 的交互。
6. 真实磁盘掉电后的持久性（`FsStore::flush` 调 `sync_all`，但掉电
   测试不在本环境执行）。
