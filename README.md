# polyeval — 乘积树 / 余式树驱动的批量多项式求值

C++20 多模块后端：给定多项式 `P(X)` 与一批求值点 `x_0..x_{n-1}`，用
**乘积树（product tree）** 与 **余式树（remainder tree）** 完成多点求值。
所有除数都是首一多项式（monic），因此算法**不做任何除法/求逆**——
重复求值点不会除零，输出严格保留请求顺序。

- 数值域：精确整数 `Z`（`boost::multiprecision::cpp_int`，无舍入）
  与有限域 `F_p`（64 位素数模，确定性素性校验），二者**不得在同一请求内混用**。
- 线性代数/卷积内核：Eigen 3.4.0（`cwiseProduct` 段乘累加）。
- 构建：CMake（项目内解包，免 root、免容器、本机原生进程）。

## 模块职责

| 模块 | 代码位置 | 职责 |
|---|---|---|
| 数值契约 | `include/polyeval/contract.hpp`, `src/polyeval/contract.cpp`, `field.cpp`, `config.cpp`, `json.cpp` | 模式、请求/响应类型、失败类别、请求文本解析、素数判定、配置解析、JSON 转义 |
| 算法内核 | `include/polyeval/kernel.hpp`, `src/polyeval/kernel.cpp`, `kernel_impl.hpp`, `eigen_support.hpp` | 首一多项式乘法/余式、乘积树、余式树、槽位与内存批次决策 |
| 误差解释 | `include/polyeval/explainer.hpp`, `src/polyeval/explainer.cpp` | 逐点结论：`EXACT` / `FIELD_RESIDUE` / `UNCERTAIN`，失败与不确定单列 |
| 编排驱动 | `src/polyeval/driver.cpp`, `log.cpp` | 模式分流、分批执行、逐点独立 Horner 交叉校验、JSONL 步骤日志 |
| CLI | `src/cli/main.cpp` | `eval` / `version`，JSON Lines 输出，退出码区分失败类别 |
| 独立基准 | `bench/bench_main.cpp` | 合成数据；树 vs 独立 Horner 基线；记录操作数与耗时 |
| 独立测试 | `tests/test_main.cpp`, `tests/framework.hpp` | 35 个断言具体值与具体失败类别的用例（参考答案独立实现） |
| 配置 | `configs/default.conf` | 内存预算、每标量字节估计、交叉校验位上限、批次 |

核心机制是真实算法，不存在硬编码演示：测试与基准的 Horner 参考均独立于内核实现。

## 算法

```
乘积树:  叶子 (X - x_i)，逐层两两相乘 => 首一 M_v
根余式:  R_root = P mod M_root
余式树:  R_child = R_parent mod M_child  （M_child 首一 => 只做乘减，无除法）
叶子:    R 为常数，等于 P(x_i)
```

- 重复点：两个叶子同为 `(X-x)`，合并得到 `(X-x)^2`，仍首一，不需要逆元。
- 复杂度：乘法为教科书式 O(degA·degB)（Eigen 段乘）。乘积树每一层
  总工作量 O(nd)，共 O(log n) 层；余式下降同理。模型复杂度为
  **O(nd log n)** 标量乘加，内存 **O(n log n)** 标量槽。
  基准会输出 `multiply_scalar_ops`/`remainder_scalar_ops`，n 翻倍时乘法
  操作数约 ×4（受度截断影响略有常数偏差），与该模型一致。
- 关键取舍：没有接入 NTT/FFT，所以不是近线性的 `O~(n+d)`。通用任意素数
  与任意整数下无法统一使用单一 NTT 友好模数；为保证“两种模式一致、精确”，
  选择精确的学校法 + Eigen 向量化。乘法层已隔离在 `kernel::multiply`，
  可替换为快速乘法而不动树结构。

## 内存分批（结果一致）

- 乘积树槽位数精确计算：`slots(n) = n·levels + 总节点数`（节点跨 s 个点
  存 s+1 个系数）。见 `kernel::product_tree_slots`。
- 预算内取最大批次 `choose_batch_size`（二分），把点切成若干块分别建树求值，
  再按原顺序拼接。`tests` 中 `batched_results_identical_to_unbatched` 对
  批次 1/2/3/7/31/100/200 断言逐点完全一致。
- 预算连单点树（2 槽）都放不下时返回 `TOO_SMALL_MEMORY_BUDGET`，不静默截断。
- `cpp_int` 每标量字节数按保守平均值（默认 32）估计，可在配置里调。

## 本机启动（Linux x86_64，原生，无 Docker）

```bash
bash scripts/setup_deps.sh   # 首次：项目内装 CMake/Eigen/Boost，含 sha256 校验
bash scripts/build.sh        # CMake 配置 + 编译到 build/
bash scripts/test.sh         # CTest：35 个单测 + CLI 冒烟
bash scripts/bench.sh        # 独立基准（JSON Lines）
```

也可手动：

```bash
tools/bin/cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
tools/bin/cmake --build build -j
(cd build && ../tools/bin/ctest --output-on-failure)
./build/polyeval version
```

依赖锁定见 `deps.lock.json`。CMake 采用 Ubuntu noble 官方 `.deb`
（`apt-get download` 后 `dpkg-deb -x` 到 `tools/cmake`，不需要 sudo），
运行时通过 `tools/bin/cmake` 包装脚本注入本地 `LD_LIBRARY_PATH`。
Boost 仅稀疏拉取 cpp_int 所需 9 个头文件库（boost-1.86.0 tag）。

## 请求格式（`examples/request_demo.txt`）

空行分隔多个请求；逐请求失败被隔离，不影响其它请求：

```
request: demo-exact
mode: exact                 # exact | field
coeff: 1 2 3                # 升幂 c0 c1 c2 ...
points: 0 1 2 3 -2 2        # 允许重复，输出保留顺序
memory_bytes: 4096          # 可选：该请求乘积树预算
batch_size: 8               # 可选：显式批次（0/省略 => 按预算自动）
crosscheck_bits: 256        # 可选：精确 Horner 校验位上限
```

域模式示例：

```
request: demo-field
mode: field
prime: 17                   # 必须为素数（确定性 64 位 Miller-Rabin）
coeff: 1 2 3
points: 0 16 2              # 元素必须是 [0,p) 内规范剩余
```

- 数字可加前缀强制模式：`F:123`（域）、`Z:-5`（精确）。在精确请求里用
  `F:` 或在域请求里用 `Z:`/负数/≥p 的值，分别判为 `MIXED_MODE` /
  `BAD_FIELD_ELEMENT`。
- 跨请求可以一个是 field、一个是 exact；同一请求内不允许。

## 示例运行

```bash
./build/polyeval eval \
  --request examples/request_demo.txt \
  --config configs/default.conf \
  --log /tmp/polyeval.jsonl --explain
```

输出为每行一个 JSON 对象，按请求/点的原始顺序排列。精确点示例：

```json
{"id":"demo-exact","mode":"EXACT","status":"OK","point_count":6,
 "points":[{"index":0,"x":"0","y":"1","exact_crosschecked":true,"status":"EXACT",
            "interpretation":"exact integer value; independent Horner cross-check agreed; no rounding"}, ...]}
```

日志 `/tmp/polyeval.jsonl` 每行关联 `request_id`、`version`、`phase`、
`location`（源码模块位置）、`status`、`detail`，可审计关键步骤：
`request → batch → build → descend/crosscheck → response`。

## 失败类别（机器可读，测试逐一断言）

| 状态 | 含义 |
|---|---|
| `OK` | 成功 |
| `PARSE_ERROR` | 语法错误、未知键、数字字面量非法 |
| `MISSING_FIELD` | 缺 `mode` / `prime` / 系数 |
| `EMPTY_POINTS` | 未提供求值点 |
| `MIXED_MODE` | 一个请求内混用 field 与 exact 标记 |
| `BAD_FIELD_ELEMENT` | 域元素不在 `[0,p)`（含负数） |
| `BAD_MODULUS` / `NON_PRIME_MODULUS` | p<2 或 p 为合数 |
| `TOO_SMALL_MEMORY_BUDGET` | 预算无法容纳单点乘积树 |
| `INVALID_CONFIG_VALUE` | 配置值非法 |
| `INTERNAL_ERROR` | 未预期异常（原因字符串回传） |

不确定结论与确定值分开：精确模式下当结果位宽超过 `crosscheck_bits`，
独立 Horner 校验被跳过，该点标为 `"uncertain":true`、解释状态 `UNCERTAIN`，
CLI 在 stderr 以 `[UNCERTAIN]` 单列；域模式结果是精确剩余，但解释会提示
“整数求值可能已回绕（wrapped）”。

## 支持范围与限制

- 支持任意次数稠密多项式（升幂系数）、任意点数（含 0 点报错、单点、非 2 的幂、全重复）。
- 域模式：素数 `p < 2^63`（乘法经 `__uint128_t`），素性判定对全部 64 位输入确定。
- 精确模式：有符号任意精度整数；大整数结果的独立 Horner 校验受位上限保护，
  超出则给不确定标记而非伪造“已验证”。
- 单线程；`ModInt` 的模数是线程局部全局槽（`ModIntScope` RAII），不同素数
  的请求不要在同一线程内交错使用裸 `ModInt` 运算（CLI 串行处理无此问题）。
- 内存预算按乘积树标量槽估计，不含输入/输出本身（属调用方数据）。
