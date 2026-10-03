# 验证报告（本机原生，非容器）

- 日期：2026-10-03
- 主机：Linux x86_64；`g++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0`，C++20
- CMake：3.28.3（官方 noble `.deb` 解包到 `tools/cmake`）
- Eigen：3.4.0（官方 tar，sha256 已锁定）；Boost.Multiprecision：1.86.0 稀疏头（9 个库，sha256 已锁定）
- 复现：`bash scripts/validate.sh`（原始输出在 `docs/runs/`）

## 构建

| 步骤 | 结果 | 证据 |
|---|---|---|
| CMake configure | rc=0 | `docs/runs/configure.log` |
| Build（-Wall -Wextra -Wpedantic） | rc=0，无警告 | `docs/runs/build.log` |

## 测试结果

- 单元/契约测试：**35/35 通过**（`docs/runs/unit_tests.log`），通过 `build/polyeval_tests`
- CTest：**2/2 通过**（`docs/runs/ctest.log`）
  - `unit_and_contract_tests`
  - `cli_smoke`（真实 CLI 进程：精确+域+重复点+混用拒绝+日志字段断言）
- CLI 示例：rc=1，属**预期**——示例文件内含 1 个故意非法请求（`MIXED_MODE`），
  两个合法请求正常求值（`docs/runs/cli_stdout.jsonl`）。

覆盖的关键断言（均断言具体值/具体失败类别，非“接口可调用”）：

- 逐点独立 Horner 参考（在测试与基准中各自独立实现，不复用内核）。
- 重复点不除零且顺序保留；零多项式、常数多项式、单点、非 2 的幂（1..100）。
- 40 组随机精确用例（含注入重复点）、域模式随机用例（p=1000000007）、回绕剩余正确性。
- 批次 1/2/3/7/31/100/200 的结果与整批逐点一致；小预算触发真实分批。
- 槽位公式、预算二分、预算过小 => `TOO_SMALL_MEMORY_BUDGET`。
- 失败类别：`MIXED_MODE`、`BAD_FIELD_ELEMENT`、`NON_PRIME_MODULUS`、`EMPTY_POINTS`、
  `MISSING_FIELD`、`PARSE_ERROR`；多请求失败隔离与顺序保持。
- 不确定性：超位宽跳过 Horner 校验 => `UNCERTAIN`；配置非法值 => `INVALID_CONFIG_VALUE`。
- 复杂度计数：乘法/余式标量操作数、槽位数均被记录并断言非零。

确定性 64 位素性判定也被直接断言（含最大 64 位素数与其相邻偶数合数）。

## 基准（合成数据，记录复杂度）

原始 JSONL：`docs/runs/bench_exact.jsonl`、`docs/runs/bench_field.jsonl`。

`multiply_scalar_ops`（乘积树标量乘计数）：

| n | ops | n 翻倍倍数 |
|---|---|---|
| 16 | 199 | — |
| 32 | 687 | 3.45× |
| 64 | 2463 | 3.58× |
| 128 | 9151 | 3.72× |
| 256 | 34943 | 3.82× |
| 512 | 135935 | 3.89× |

趋近 4×，与学校法乘法下“每层 O(nd)、共 O(log n) 层”的 O(nd log n) 模型一致。
`remainder_scalar_ops` 每层按 d 累加，按 n 近线性翻倍。基准同时用独立 Horner
对首/中/尾三个点做正确性比对，失配以退出码 3 失败。

时序（最佳重复，毫秒）随机器波动；在本机 n≤256 的小 d 场景下 Horner 基线
绝对耗时更低（d 较小、无建树常数），树法的渐近优势需在 d 与 n 同阶且更大、
或替换为快速乘法后才体现——这是本实现已记录的明确取舍。

## 失败 / 未执行 / 未实现项（如实列出）

- 未实现 NTT/FFT 快速多项式乘法：当前为精确学校法 + Eigen 向量化，模型
  复杂度 O(nd log n) 而非近线性 O~(n+d)。乘法隔离在 `kernel::multiply` 以便替换。
- 未做多线程：`ModInt` 使用线程局部全局模数槽（`ModIntScope`），跨不同素数
  的裸域运算不可在同线程交错；CLI 串行，无实际触发路径。
- 未执行容器化验证：题目明确禁止 Docker/容器，全部为本机原生进程。
- 未使用/未验证 Windows PowerShell 路径：脚本仅在本机 Linux bash 实测
  （PowerShell 为允许项而非必需）。
- 未做流式/超大 n（百万级点）的端到端长跑：分批机制已用中小规模断言一致性，
  极大规模仅受预算与 cpp_int 内存估计精度影响（后者为保守平均估计，非精确计量）。
- 开发过程中的临时纵向切片产物（`build-slice/`、`dl/` 探针）已通过 `.gitignore`
  排除，不属于交付目标；中途曾出现两次内核 bug（乘积树层重分配悬垂引用、
  move 后层级被清空），均由 ASan/UBSan 定位修复并回归通过。
