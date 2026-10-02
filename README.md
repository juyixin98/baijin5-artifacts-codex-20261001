# Pade 有理逼近工程（C++20 / CMake / Eigen）

给定幂级数 `f(x) = Σ c_k x^k` 与阶数 `(m, n)`，计算 Padé 有理逼近

```
R(x) = A(x) / B(x),   deg A ≤ m, deg B ≤ n, B(0) = 1
```

满足逐项匹配（order matching）：

```
r_k = (C*B)_k - a_k = 0,   k = 0 .. m+n
(C*B)_k = Σ_{j=0..min(k,n)} c_{k-j} b_j
```

工程按 **数值契约 / 算法内核 / 误差解释 / 独立基准** 分层，含独立测试层
与本地配置/依赖层。无容器、无系统安装、无外部业务数据；所有夹具由本
仓库 C++ 程序本地合成。

## 目录结构

```
include/pade/        数值契约层（公共类型、错误语义、求解/求值/IO 接口）
  types.hpp          StatusCode、Options、PadeResult、RankDiagnostics 等
  polynomial.hpp     多项式原语（卷积、截断、近似 GCD、Horner）
  solver.hpp         算法内核接口：padeApproximate / evaluatePade / verifyResiduals
  io.hpp             本地夹具解析与报告渲染
src/                 实现层
  solver.cpp         Toeplitz 分母块 + SVD 秩诊断 + 规范化 + 约化 + 残差
  polynomial.cpp     近似多项式 GCD（带容差的欧几里得算法）等
  io.cpp, main.cpp   文本夹具、可运行服务入口 pade_cli
tests/               独立测试层（自带最小断言框架，参考答案为手算分数）
bench/bench_pade.cpp 独立基准：Padé vs 截断 Taylor（对照 std::exp 闭式）
app/gen_fixtures.cpp 本地合成数据生成（C++）
data/                生成的 .series 夹具（运行 demo 后出现）
scripts/             setup_deps.sh / build.sh / test.sh / demo.sh
third_party/         项目内 CMake 与 Eigen（脚本自动获取，免 root）
```

## 快速开始（Linux x86_64，原生进程）

```bash
bash scripts/setup_deps.sh   # 首次：下载并解压 CMake 3.28.3 与 Eigen 3.4.0
bash scripts/build.sh        # 原生 Release 构建到 build/
bash scripts/test.sh         # ctest 执行独立测试套件（15 个用例，135 断言）
bash scripts/demo.sh         # 端到端演示：夹具生成→求解→退化→极点→服务→基准
```

也可手动：

```bash
./third_party/cmake-bin/cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
./third_party/cmake-bin/cmake --build build -j"$(nproc)"
./build/pade_tests
```

依赖获取细节见 `DEPENDENCIES.md`。CMake 以 Ubuntu 官方 `.deb` 包
（`apt-get download`，不安装、免 root）解压到 `third_party/` 提供；
Eigen 为上游 3.4.0 头文件。

## 服务入口 `pade_cli`

```bash
# 一次性求解：输出带 run_id/版本/逐步计算/逐项残差/判定的报告
build/pade_cli solve --file data/exp12.series --m 4 --n 4 --run-id my-run

# 单点求值（近极点显式失败类别，见下）
build/pade_cli eval --file data/geometric10.series --m 1 --n 1 --x 1

# stdin 行协议服务（每行携带输入与序号，输出关联 run_id/input）
printf 'SOLVE data/exp12.series 3 3\nEVAL data/exp12.series 3 3 0.3\nQUIT\n' \
  | build/pade_cli serve --run-id srv-1
```

夹具格式（纯文本，无外部格式依赖）：

```
# 注释
name = exp12
coefficients = 1, 1, 0.5, 0.1666..., ...
```

## 错误语义（显式状态，绝不把异常/未知统一成成功）

内核不抛异常，所有路径返回 `StatusCode`：

| 状态 | 触发条件 | 处置/进程退出码 |
|---|---|---|
| `OK` | 规范化成功且 `r_0..r_{m+n}` 逐项满足容差 | 0 |
| `INVALID_ARGUMENT` | 阶数为负、系数为空或含 NaN/Inf、夹具非法 | 2 |
| `INSUFFICIENT_COEFFS` | 系数少于 `m+n+1` | 3 |
| `RANK_DEFICIENT` | 分母块数值零空间维数 `nullity>1`（Froissart/块缺陷）；仍返回**规范化的确定性最佳代表**与完整诊断 | 10 |
| `NORMALIZATION_IMPOSSIBLE` | 零空间代表的 `b0` 为 0（在容差内），分母常数归一条件不可满足；保留未缩放齐次解、原始阶数槽位、约化信息与残差 | 11 |
| `POLE_EVALUATED` | 求值点 `|B(x)|` 低于 `near_pole_tol`（绝对零点返回 `inf`） | 20 |
| `IO_ERROR` / `INTERNAL_ERROR` | 文件不可读 / 不应到达的路径 | 2 / 1 |

关键不变量：

- **归一不可满足时标退化**：唯一（或整个）零空间代表 `b0≈0` 时，状态为
  `NORMALIZATION_IMPOSSIBLE`，不会偷偷把分母常数改成 1 再报告成功。
- **公因子消除不抹去局部定义信息**：`numerator_full` / `denominator_full`
  始终是请求阶数 `[m/n]` 下的完整系数向量（含零槽位），约化结果单独存放
  在 `numerator_reduced` / `denominator_reduced`，并报告
  `monomial_shift`、近似多项式 GCD 次数与约化后阶数。
- **匹配阶数逐项核验**：报告打印每个 `r_k`、`max|r_k|`，以及首个失控项
  `r_{m+n+1}`（一般非零），判定写为 `MATCH_TO_ORDER` 或具体失败类别。

### 退化处理细节

分母方程组取行 `k=m+1..m+n`，构成 `n×(n+1)` 的 Toeplitz 块
`B_{i,j}=c_{m+i-j}`。用 JacobiSVD 得到数值秩、零空间与有效奇异值阈值；
当 `nullity>1` 时，在零空间内从 `b_n,b_{n-1},…,b_1` 依次贪心消去坐标，
选择**确定性的最低分母次数可规范化代表**（原始零空间基保留在
`rank.nullspace` 供审计）。

手算退化示例（均在测试中断言具体结果与类别）：

- `f=1` 请求 `[1/2]`：块秩 1、零空间维数 2，规范化代表给出精确 `1/1`，
  状态 `RANK_DEFICIENT`。
- `f=1+x²` 请求 `[1/1]`：唯一零向量 `b*=(0,1)`，`b0=0` →
  `NORMALIZATION_IMPOSSIBLE`，原始齐次解与约化信息保留。
- `f=1+x` 请求 `[2/2]`：Froissart 块缺陷，规范化最小代表为 `(1+x)/1`，
  但 `[2/2]` 槽位的完整系数向量仍保留。
- `1/(1-x)` 请求 `[1/1]`，在 `x=1` 求值：`POLE_EVALUATED`，返回 `inf`。

## 独立测试与参考答案

测试框架为 `tests/test_framework.hpp`（无第三方测试库）。参考答案是
**手算精确有理数**或与被测内核无关的来源：

- exp(x) `[1/1]`：`(1+x/2)/(1-x/2)`，首失控项 `(CB-A)_3=-1/12`。
- exp(x) `[2/2]`：`(1+x/2+x²/12)/(1-x/2+x²/12)`，`(CB-A)_5=+1/720`。
- 秩亏、归一不可能、公因子、近极点、夹具解析、独立残差 oracle 均有
  具体数值与失败类别断言。
- `bench/` 独立用 `std::exp` 闭式对比 Padé 与截断 Taylor 误差。

日志可关联身份与输入：测试二进制打印 `run_id`（可用环境变量
`PADE_RUN_ID` 指定）与版本；CLI 报告含 `run_id`、`input`、`version`、
秩诊断、规范化、约化、逐项残差与最终判定。

## 复现步骤（干净环境）

```bash
bash scripts/setup_deps.sh
bash scripts/build.sh
bash scripts/test.sh    # 期望：100% tests passed
bash scripts/demo.sh    # 观察退出码 0 / 11 / 20 与服务行协议输出
```
