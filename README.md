# 任意长度复数 FFT：Bluestein 降级 + 逆变换（C++20 / CMake / Eigen）

一套多模块原生 C++20 后端。对任意长度的复数序列，当长度是 2 的幂时走
radix-2 Cooley–Tukey；否则降级到 Bluestein 算法，并提供固定归一化的逆变换。

## 数值契约（固定约定）

- 正变换（未归一化）：`X[k] = Σ_n x[n] · exp(−i·2π·n·k/N)`
- 逆变换（固定 `1/N`）：`x[n] = (1/N) Σ_k X[k] · exp(+i·2π·n·k/N)`
- 卷积补零：选择满足 `M ≥ 2N−1` 的最小 2 的幂，保证得到的是**线性**卷积，
  而非循环卷积；chirp 滤波器中间段保持为零。
- chirp 大索引相位：`j²` 只用 64 位整数在模 `2N` 下递推（增量 `2j+1`），
  送进 `sin/cos` 的角度始终落在 `[−2π,2π]`，不随 `N` 增长，从而控制大
  索引误差。

## 模块职责（核心机制非硬编码演示）

- `include/fft/types.hh` / `src/types.cc` —— 数值契约：错误码、请求身份、
  内存记账、步骤跟踪。
- `include/fft/kernel.hh` / `src/kernel.cc` —— 算法内核：迭代 radix-2 FFT 与
  任意长度 Bluestein（含补零、chirp、频域卷积、逆变换归一化）。
- `include/fft/errors.hh` / `src/errors.cc` —— 误差解释：失败类别语义
  （含义/可否重试/处置），并把“不确定结论”（roundoff/elevated/untrusted）
  与硬失败分开。
- `include/fft/benchmark.hh` / `src/benchmark.cc` —— 独立基准：合成夹具、
  计时、残差统计、内存（确定性记账 + `getrusage` RSS）、不确定性裁决。
- `src/main.cc` —— 可运行服务入口（本地 CLI）。
- `bench/bench_main.cc` —— 独立基准可执行程序。
- `test/` —— 独立测试（自带小型断言框架），与配置 `CMakeLists.txt`、
  脚本 `scripts/` 分离。

## 参考答案的独立性

被测内核**不**用于生成参考：

- `test/naive_dft.hh`：独立的教科书式 O(N²) 直接 DFT（仅用 `<complex>`）。
- 冲激等用例使用解析式参考（移位冲激的 DFT 是模为 1 的纯指数）。
- Eigen `Eigen::FFT` 作为独立第三方实现做交叉校验（与被测内核是两套代码）。

## 构建与验证（原生 Shell，无容器、无额外运行时）

```sh
scripts/setup_deps.sh     # 仅下载本地 CMake 与 Eigen 到 ./cmake/
scripts/verify.sh         # 配置 + 构建 + ctest + CLI 冒烟 + 基准
```

手动等价命令：

```sh
./cmake/cmake-3.30.5-linux-x86_64/bin/cmake -S . -B build-cmake -DCMAKE_BUILD_TYPE=Release
./cmake/cmake-3.30.5-linux-x86_64/bin/cmake --build build-cmake -j$(nproc)
(cd build-cmake && ctest --output-on-failure)
```

## 服务入口用法

```sh
./build-cmake/fft_cli --length 977 --direction fwd \
    --generator impulse --request-id demo-977 --trace --show 3
```

- 生成器：`random` | `impulse` | `large-dynamic`（全部本地合成、确定性 seed）。
- 输出带请求 id、版本、处理位置、关键步骤（`--trace`）、算法与卷积长度、
  内存、相对/绝对/RMS 误差，并把不确定性单列在 `UNCERTAINTY` 行。

退出码：`0` 成功且可接受；`1` 变换硬失败；`2` 用法错误；
`3` 结果不确定/不可信（如缺少独立参考）。

## 错误语义（结构化 `ErrorCode`，测试按类别断言）

| ErrorCode | 含义 | 可重试 |
|-----------|------|--------|
| `EMPTY_LENGTH` | 长度为 0 | 否 |
| `LENGTH_OVERFLOW` | 卷积补零长度/索引溢出可表示范围 | 否 |
| `ALLOCATION_FAILED` | 工作缓冲区分配失败 | 是 |
| `NAN_OR_INF_INPUT` | 输入含 NaN/Inf，输出未定义 | 否 |
| `UNSUPPORTED_LENGTH` | 长度或缓冲形状不支持 | 否 |
| `INTERNAL_ERROR` | 内核不变量被破坏或出现非有限输出 | 否 |

“误差偏大但在 100 倍保护带内”归为 `elevated`（不确定、可接受、单列），
缺少独立参考归为 `untrusted`，二者都不与上表硬失败混淆。

## 验收用例（实际执行，报告误差与内存）

- 小长度直接 DFT 参考逐 bin 比对；素数长度（2…977，另含 200003 大素数冲激）。
- 冲激/移位冲激（大索引 chirp 相位，解析参考）。
- 大动态输入（1e8 / 1 / 1e-7 混合，采用缩放容差）。
- 正逆往返、正变换未归一化/逆变换恰为 1/N、Eigen 第三方交叉校验。
- 每类非法输入断言**具体** `ErrorCode`，而非“接口能调用”。

测试与基准会打印断言数、失败数、最大绝对/相对误差、RMS 与工作集/RSS 内存。
