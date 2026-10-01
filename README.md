# set_ops — 类型化批式 UNION / INTERSECT / EXCEPT（DISTINCT & ALL，支持超内存批次）

Rust + Axum + Arrow2 实现的集合算子引擎。六个算子（3 操作 × 2 限定符）在
**内存** 与 **外存（分区溢写 + 递归再分区）** 两条路径下产出逐行一致的多重集结果。

## 核心语义契约

| 算子 | DISTINCT | ALL |
|------|----------|-----|
| UNION | 出现在任一侧 → 1 行 | `count(L) + count(R)` |
| INTERSECT | 两侧都出现 → 1 行 | `min(count(L), count(R))` |
| EXCEPT | 在 L 且不在 R → 1 行 | `max(0, count(L) − count(R))` |

- **NULL 相等语义固定**：NULL == NULL（GROUP BY 语义），NULL 永不等于非空值；
  多列下 NULL 位置不同即不同行。
- **行编码区分列边界与类型**（`src/batch/encode.rs`）：每个值带类型标签
  （bigint/double/bool/text 互不相同），变长文本带 u32 长度前缀，列尾有显式
  边界标记。`("12","3")` 与 `("1","23")`、`1_i64` 与 `"1"` 均不可能同键。
  f64 规范化（所有 NaN 同模式、−0 == +0）。
- **哈希碰撞必须真实行比较**：哈希只决定分区桶；计数表以完整规范键字节为键，
  `Hash` 之外另有逐字节 `Eq`。
- **ALL 是多重集算术**，不是去重；所有计数加法均为 checked arithmetic 并受
  `max_count` 上限约束，溢出以 `resource_exhausted/count_overflow` **拒绝**。
- **倾斜分区**由递归级独立哈希（SplitMix64 + 级别盐）打散；重复行倾斜在
  聚合阶段坍缩为一行一个计数；递归深度超限给 `partition_depth`。

## 工程结构（数据/错误契约按模块归属）

```text
src/
  batch/       类型化批次：schema、规范行编码、解码、CSV 夹具、JSON 行
  operator/    查询算子：counts(计数表+多重集算术)、exec(GRACE 分区执行引擎)
  resource.rs  资源预算（内存字节、分区扇出/深度、计数上限、溢写字节、输出上限）
  spill.rs     带校验和的溢写帧（魔数/结束标记/FNV 校验/计数校验，损坏→状态冲突）
  runlog.rs    可重放运行日志（run_id、内存决策、溢写、计数算式、裁决 JSONL）
  service/     Axum 验证/执行入口（DTO、错误信封、夹具路径沙箱）
  error.rs     四类可区分错误：input / state_conflict / resource_exhausted / compute
  main.rs      CLI：serve / run / fixture
tests/
  common/      独立多重集参考实现（自带 CSV 解析器，不共享被测代码）
  set_ops_test.rs  正确性 + 失败类别 + 溢写 + 倾斜 + 碰撞测试
  api_test.rs      真 TCP 端到端 HTTP 测试
scripts/
  curl_examples.sh 服务调用示例（正常 + 各类失败）
  verify_e2e.py    独立 Python 参考逐行多重集比对（6 算子 × 3 模式 × 2 数据集）
fixtures/      最小合成夹具
docs/          复现文档与已保留的真实运行结果
```

## 构建与测试

```bash
cargo build --release
cargo test                       # 38 个测试：单元 + 算子 + HTTP
cargo clippy --all-targets       # 零警告
cargo fmt
```

依赖锁定见 `Cargo.lock`。工具链：Rust stable（见 `rust-toolchain.toml`）。

## 快速使用

CLI：

```bash
# 内存模式
cargo run -- run --left fixtures/edge_left.csv --right fixtures/edge_right.csv \
  --op EXCEPT --qualifier all --mode in-memory

# 4KB 驻留预算，强制走真实外存溢写
cargo run -- run --left L.csv --right R.csv --op UNION --qualifier all \
  --mode auto --memory-bytes 4096 --fanout 4 --spill-dir ./spill

# 生成合成压力数据（固定 xorshift 种子，可复现）
cargo run -- fixture --out fixtures/generated --rows 20000 --keyspace 200 --kind all
```

服务：

```bash
cargo run -- serve --addr 127.0.0.1:8080 --fixture-root fixtures --spill-root ./spill
bash scripts/curl_examples.sh
```

执行与复现细节见 [`docs/REPRODUCE.md`](docs/REPRODUCE.md)。
