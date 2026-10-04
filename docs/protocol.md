# IBLT 二进制线格式（版本 1）

所有整数为小端。文件 = 32 字节定长头 + `n_cells` 个 24 字节单元。

## 头部（32 字节）

| 偏移 | 大小 | 字段 | 约束 |
|---|---|---|---|
| 0  | 4 | magic = `"IBLT"` | 必须完全匹配 |
| 4  | 2 | version = `1` | 其他版本 → `invalid_input` |
| 6  | 2 | flags = `0` | 非零 → `invalid_input` |
| 8  | 4 | k | 1..=8，且 ≤ n_cells |
| 12 | 4 | n_cells | ≥ k；超过配置上限 → `resource_exhausted` |
| 16 | 8 | seed | 哈希种子 |
| 24 | 8 | reserved = `0` | 非零 → `invalid_input` |

## 单元（每个 24 字节）

| 偏移 | 大小 | 字段 | 类型 |
|---|---|---|---|
| 0  | 8 | count    | i64（有符号；差表中负值 = 反向差的键） |
| 8  | 8 | key_sum  | u64（键的 XOR） |
| 16 | 8 | hash_sum | u64（键校验和的 XOR） |

## 解析规则（严格）

1. 缓冲区不足 32 字节 → `invalid_input`（truncated header）。
2. magic / version / flags / reserved 任一不符 → `invalid_input`。
3. `n_cells` 超过 `Limits::max_cells` → `resource_exhausted`。
4. 缓冲区长度必须**恰好**等于 `32 + 24 * n_cells`，
   截断或拖尾 → `invalid_input`（length mismatch）。
5. 参数合法性（k 范围、cells ≥ k）由内核 `Iblt::from_cells` 复核。

## 确定性

同一表状态序列化为同一字节串：哈希只依赖 `(seed, k, cells)` 与键本身，
无随机状态。`tests/format_interop.rs` 用提交的夹具逐字节锁定该性质，
格式或哈希函数的任何漂移都会使测试失败。

## 哈希方案（与格式绑定的语义）

- 下标：`splitmix64(key ^ seed ^ attempt * GOLDEN) % cells`，
  递增 `attempt` 拒绝采样直到取得 k 个**互异**下标。
- 校验和：`splitmix64(key ^ seed ^ CHECKSUM_TAG)`。
- 纯单元：`count == ±1` 且 `hash_sum == checksum(key_sum)`
  且当前下标 ∈ `indices(key_sum)`。
