# 乘积树 / 余式树驱动的批量多项式求值（multipoint-eval）

一套多模块 C++20 后端：用**子乘积树（subproduct tree）**构造点因式
`M(x)=∏(x-xᵢ)`，再用**余式树（remainder tree）**沿树逐层取余，批量求
`f(x₁),…,f(xₙ)`。支持两种互不混用的代数域：

- `INTEGER`：`boost::multiprecision::cpp_int` 精确无界整数；
- `FIELD`：运行时素数模 `p < 2^63`（数位存于 `uint64`，`__uint128` 乘法约减）。

多项式乘法使用 Karatsuba，单子多项式除法用“反转 + Newton 级数求逆”，
多项式运算层面为亚二次（约 `O(n^1.58)`），显著区别于逐点 Horner 的 `O(n²)`。

## 关键机制（非硬编码演示）

- **重复点不除零且保序**：每个点只生成一个线性因子 `(x-r)`，重复点作为
  独立求值项复用同一叶子；树中除数始终首一，重复根不会产生零因子或非常元
  首项。结果带原始索引 `idx` 重组，重复点、顺序完全保留。
- **域不混用**：契约层在进入内核前强制每作业单一域；`INTEGER` 带 `MOD`、
  `FIELD` 缺模/合数模/越界模等分别归类为稳定失败码（见下表），不会静默
  落到另一套数位运算。
- **内存超限可分批，结果一致**：按树的解析槽位估算峰值内存，二分求出能装入
  上限的最大批大小，超限自动分多批；各批结果按原索引拼接，分批对输出不可见。
  单批都放不下时报 `INFEASIBLE_BATCH_LIMIT`。

## 模块职责

| 模块 | 路径 | 职责 |
|---|---|---|
| 数值契约 | `src/numcontract` | 行式请求/配置语法、域隔离、素性判定（确定性 Miller–Rabin，覆盖 uint64）、失败分类、内存可行性 |
| 算法内核 | `src/core` | 环数位（整数/域）、Karatsuba、单子长除与级数求逆、乘积树/余式树、分批调度、独立 Horner 交叉校验 |
| 误差解释 | `src/explain` | 关联请求/作业身份与代码位置的步骤轨迹，确定结果 / 失败 / 不确定结论三分段，文本与 JSON 报告 |
| 独立基准 | `src/polybench` | 仅本地合成数据；树 vs 独立 Horner 计时，Eigen 最小二乘拟合复杂度指数，产出 CSV+Markdown |
| 应用入口 | `src/app` | CLI：加载配置、解析、校验、调度、出报告 |
| 独立测试 | `tests` | 独立测试框架 + **第三种**逐点 Horner 参考，断言具体值与失败类别 |

## 目录与启动（本机原生，禁止容器）

依赖全部锁版本、校验 SHA256 并解压到项目内 `deps/`，不需要 root/apt：

- CMake `3.30.5`（官方预编译二进制）
- Eigen `3.4.0`（头文件，仅基准用）
- Boost `1.86.0`（纯头文件 multiprecision）

锁与校验见 `tools/deps.lock`、`tools/fetch_deps.sh`。Shell 脚本：

```bash
./build.sh        # 首次会下载并校验依赖到 deps/，然后原生编译到 build/
./test.sh         # 单元测试 + CLI 集成测试（ctest）
./bench.sh        # 两个域的本地合成基准，刷新 bench/*.csv 与 bench/*.md
```

Windows / PowerShell 7 下等价入口为 `./run.ps1 -Task build|test|bench`
（需在 Windows 上取得同名锁版本依赖；本仓库开发与验证在 Linux x86_64）。

直接调用二进制：

```bash
./build/mp_eval --request examples/requests/multi_job.req            # 文本
./build/mp_eval --request examples/requests/failures.req --format json --steps
./build/mp_eval --request examples/requests/smoke_int.req \
               --config <(printf 'memory_limit = 4000\nbytes_per_slot = 12\nmemory_fudge = 1.3\n') --steps
```

## 请求格式（行式，严格）

```
REQUEST <id>
JOB <id>
DOMAIN INTEGER|FIELD
MOD <十进制素数>        # 仅 FIELD，必须素数且 < 2^63
COEFF c_k c_{k-1} ... c_0
POINTS x1 x2 ...        # 允许重复，顺序有意义
```

空行与 `#` 注释被忽略。系数/点均为十进制整数字面量（可负、可超过模，域模式
自动约减）。一个请求可含多个作业；**跨作业**可分别使用不同域，**作业内**
不得混用。配置（INI，全部可省略）见 `config/default.ini`。

## 失败类别（稳定码，测试按码断言）

`MALFORMED_REQUEST`、`DUPLICATE_JOB_ID`、`MALFORMED_COEFFICIENT`、
`MALFORMED_POINT`、`EMPTY_COEFFICIENTS`、`INVALID_MODULUS`、
`NON_PRIME_MODULUS`、`MODULUS_TOO_LARGE`、`DOMAIN_MISMATCH`、
`INFEASIBLE_BATCH_LIMIT`、`INTERNAL_ERROR`。

报告中 `RESULT`（确定结果）、`FAILURE`（终结失败）、`UNCERTAIN`（不确定
结论）严格分段；`STEP` 行带 `[request/job] module@file:line step :: detail`，
VERSION 行给出语义化版本与 git 描述。

## 支持范围与关键取舍

- 支持：稠密一元整系数多项式，整数精确求值或素数域求值；重复/无序点；
  零多项式、常数多项式；大整数系数与大点数；内存驱动的透明分批。
- 域模式数位有界，树运算呈亚二次，是该机制的主要收益区间。
- **精确整数模式的取舍**：乘积树节点系数的位宽随规模增长，墙钟时间含
  位宽增长因子，在中小规模可能不如逐点 Horner。基准会把这种情况单列为
  `UNCERTAIN TREE_NOT_FASTER_AT_SCALE`，而不是掩盖。对允许有界数位的场景，
  优先使用 `FIELD`。
- 内核对每个结果都用独立逐点 Horner 复算做防御性交叉校验；任何不一致以
  `TREE_HORNER_CROSSCHECK_MISMATCH` 不确定项单列，绝不静默改写结果。

## 验证产物

- `bench/complexity_field.md`、`bench/complexity_integer.md`：实测复杂度与解释。
- 单元测试 21 例，覆盖重复点、零多项式、批次大小变化、具体数值与失败类别；
  参考期望值由独立 Horner（与内核不共享多项式/树代码）生成。

详见 `docs/DESIGN.md`。
