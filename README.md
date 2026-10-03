# 任意长度复数 FFT — Bluestein 降级与逆变换（C++20 / CMake / Eigen）

一套多模块后端：用 **Bluestein chirp-z** 算法把任意长度 DFT 降级为
卷积，并用 radix-2 FFT 计算该卷积；对 2 的幂长度直接走 radix-2 快速路径。
逆变换采用固定的 `1/N` 归一化。正确性由一个**完全独立的 long double
朴素 O(N²) DFT** 作为参考裁判，而不是由被测内核自证。

版本：`1.0.0`（见 `src/common/version.hpp`，所有日志与报告都带版本号）。

## 行为契约

1. **卷积补零长度满足线性卷积。** 任意 N 时取最小的 2 的幂 `L >= 2N-1`，
   chirp 的偶延拓保证 `L` 点圆周卷积等于线性卷积（见
   `src/contract/numeric_contract.cpp` 的 `checkConvolutionGeometry`）。
2. **chirp 相位控制大索引误差。** 需要的相位是 `pi n²/N mod 2π`。实现先用
   128 位整数精确计算 `n² mod 2N`，再转 long double 并对 `2π` 取模，
   三角参数恒在 `[0,2π)` 内，避免大 n 下 `n²` 浮点损失和 libm 大角度归约。
   `tests/test_chirp_phase.cpp` 用**另一种**算法（逐位 64×64→128 竖式乘法
   求模 + long double 递推）独立验证 n 到 `1e12` 的误差 <= 1e-15，并演示
   "朴素全角 long double" 确实会漂移（>1e-6），证明该设计是必要的。
3. **正逆归一化约定固定。**
   - 正变换：`X[k] = Σ x[n] exp(-i2πnk/N)`（**不**缩放）
   - 逆变换：`x[n] = (1/N) Σ X[k] exp(+i2πnk/N)`
   因此 `ifft(fft(x)) == x`。

## 模块职责（多模块后端，无硬编码演示）

| 模块 | 路径 | 职责 |
|------|------|------|
| 数值契约 | `src/common`, `src/contract` | 状态枚举、请求身份、误差度量与**失败分类**（Ok / PrecisionRisk / ReferenceMismatch 等）、卷积几何检查 |
| 算法内核 | `src/mathcore` | radix-2 Cooley–Tukey、Bluestein 降级、chirp 大索引相位、Eigen 门面 |
| 误差解释 | `src/contract` | 对照独立参考给出 maxAbs/rms/相对误差/最坏 bin/条件数，并把失败原因与"不确定结论"单列 |
| 独立基准 | `src/bench` | 合成夹具、解析真值（冲激/单频/常数）、计时、RSS/工作集内存报告 |
| 独立参考 | `src/reference` | long double 朴素 DFT 定义式，永不调用 mathcore |
| 服务入口 | `src/service` | `fft_service` CLI（fft/ifft/verify/generate/demo/bench）、二进制线格式、JSON 响应、结构化日志 |
| 独立测试 | `tests/` | 13 个 CTest 可执行文件，断言**具体数值与具体失败类别** |

参考裁判与被测内核的代码路径完全分离：参考模块只按 DFT 定义做 long double
双重循环；大长度另用夹具的解析闭式（如冲激 DFT、单复音 DFT）接受，不依赖
被测实现生成答案。

## 构建与复现

纯本机原生进程，禁止容器。仓库自带获取脚本，把 CMake/Eigen 下载到
`third_party/`（无需 root/apt）：

```bash
bash scripts/fetch_deps.sh   # 下载 CMake 3.30.5 + Eigen 3.4.0 到 third_party/
bash scripts/build.sh        # 用 third_party/cmake 配置并编译
bash scripts/run_tests.sh    # CTest 执行全部独立测试并报告
bash scripts/demo.sh         # 本地端到端演示 + 基准
```

也可手动：

```bash
third_party/cmake/bin/cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
third_party/cmake/bin/cmake --build build -j"$(nproc)"
(cd build && ctest --output-on-failure -j"$(nproc)")
./build/fft_service demo --n 13
```

## 服务入口

`fft_service` 子命令：

- `fft --in A.cft --out B.cft [--request-id ID] [--memory-budget BYTES]`
- `ifft --in B.cft --out C.cft [options]`
- `verify --in A.cft [--ref-max-n K]`：对照独立 long double DFT；N>K 时
  **不假装通过**，而是返回 `PRECISION_RISK` + `uncertain:true`（只做了
  结构/往返检查，属于不确定结论）。
- `generate --kind impulse|constant|tone|cosine|large-dynamic|pseudorandom --n N --out F.cft [--param P] [--seed S]`
- `demo [--n N]`、`bench [--ref-max-n K]`

二进制线格式（本地合成数据，纯 C++ 生成/读取，无其它语言运行时）：

```
"CFT1" | uint64 LE 长度 N | N × (float64 LE 实部, float64 LE 虚部)
```

## 日志与可解释性

每个请求带 `requestId`（可用 `--request-id` 显式传入以便关联）。日志单行：

```
[INFO] [fft-1] [mathcore.bluestein@1.0.0@bluestein:L=256] transform ok ...
```

JSON 响应把**失败原因**（`failureReason`）和**不确定性**（`uncertain`）
与成功详情（`detail`）分开；`status` 是稳定枚举字符串，客户端应按类别
分支而非解析文本。

## 错误语义（退出码与类别）

| 类别 | 触发条件 | 服务退出码 |
|------|----------|-----------|
| `OK` | 成功 | 0 |
| `EMPTY_INPUT` | 长度 0 | 2/3 |
| `INVALID_LENGTH` | 长度参数非法/卷积尺寸溢出 | 2/3 |
| `INVALID_ARGUMENT` | 方向非 ±1、文件缺失/格式错 | 2 |
| `VALUE_OUT_OF_DOMAIN` | 输入含 NaN/Inf | 3 |
| `ALLOCATION_FAILED` | 估计工作集超过内存预算 | 3 |
| `PRECISION_RISK` | 误差在 pass 带与硬上限之间，或 verify 超过参考长度 | 4 |
| `REFERENCE_MISMATCH` | 与独立参考误差超硬上限 | 4 |
| `INTERNAL_ERROR` / `NOT_IMPLEMENTED` | 不变量破坏/未实现路径 | 3 |

误差分带（`ContractTolerances`）：`maxAbs <= 1e-8` 通过；`<= 1e-4` 为
`PRECISION_RISK`；`<= 1e-2` 仍为不确定；更大为 `REFERENCE_MISMATCH`。
解析夹具用随尺度 N 缩放的容差。

## 测试（必须实际执行）

`bash scripts/run_tests.sh` 运行 13 个 CTest 套件（300+ 条断言），覆盖：

- 小长度 1..32 全量对拍独立参考 + 故意篡改 bin 断言 `REFERENCE_MISMATCH`
- 素数长度（2..251 对拍；5003 用冲激闭式）强制走 Bluestein
- 冲激在不同位置/长度的闭式相位与 DC=1
- 大动态输入（强音 + 弱 1e-12 音），小 N 对拍 long double，大 N 断言弱音
  bin 仍可分辨
- 正逆归一化、对独立逆 DFT、`DFT(ones)[0]=N`
- 卷积几何 `L>=2N-1` 且为最小 2 的幂（200 个长度 + 定点）
- chirp 大索引相位（独立竖式乘法/递推 oracle + 朴素法失效对照）
- 全部失败类别（空输入/坏参数/NaN/Inf/内存预算）
- 工作集记账与 `/proc/self/status` RSS 报告
- 请求身份/版本/位置与失败原因单列
- CLI 端到端管线（落盘 CFT1 往返）与"超参考长度 → 不确定"

## 验收示例结果（本机 g++ 13.3 / Release）

- N=13（素数）Bluestein，L=32：对独立 long double DFT `maxAbs ≈ 3.6e-15`，
  往返 `≈ 1.8e-15`，工作集 1440 B。
- N=5003（素数）Bluestein，L=16384：往返 `≈ 1e-12`，前/反向约 4.9 ms，
  工作集约 668 KB。
