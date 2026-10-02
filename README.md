# 带幂迭代的随机低秩矩阵分解（Randomized Low-Rank Matrix Factorization）

C++20 / CMake / Eigen 后端工程。实现 Halko–Martinsson–Troppen 风格的**随机 SVD**：
高斯随机草图 + 带**再正交化**的幂迭代（power iterations with reorthogonalization）+
小矩阵精确 SVD 后处理。所有输入均为**本地合成夹具**，无外部业务数据、无账号、无容器。

## 数值契约（明确区分三类量）

对 `A ≈ U S Vᵀ`，工程始终分别报告，绝不混为一谈：

1. **估计残差（ESTIMATED）** `est_residual_frob`：高斯探针（Hutchinson 型）
   对 `‖A − USVᵀ‖_F` 的**无偏随机估计**。不构造残差矩阵，只做矩阵–向量积。
   它是估计量，有方差，**不是精确值**。
2. **精确残差（EXACT）** `exact_residual_frob`：直接计算 `‖A − USVᵀ‖_F`。
   这是本次分解真实达到的误差（可按需关闭以用于纯计时）。
3. **SVD 最优尾部（参考下界）** `reference_tail_frob`：由**独立全 SVD**
   （测试夹具用 `JacobiSVD`，大矩阵用 `BDCSVD`）算出的
   `sqrt(Σ_{i>r} σ_i²)`，即秩-r 的理论最优 Frobenius 误差下界。
   随机方法是**近似方法，不宣称精确最优**；断言只要求
   `exact ≥ optimal`（不可能更优）且落在显式上界内。

固定项（合同要求）：

- 默认种子 `seed = 0x243f6a8885a308d3`（`mt19937_64` 语义 + 固定 Box–Muller
  变换，见 `GaussianStream`，重放逐位一致）。
- 默认过采样 `oversampling p = 10`（草图列数 `ℓ = k + p`）。
- 默认幂迭代 `power_iters q = 2`；**每一步都做两次 QR / MGS 再正交化**。
- 首次 QR 使用**列选主 QR（ColPivHouseholderQR）+ 两遍 MGS**：
  普通 Householder QR 对零矩阵仍返回单位列，无法揭示数值秩；
  列选主 QR 能在零矩阵/秩不足时给出正确的数值秩。

## 模块边界

| 文件 | 职责 |
| --- | --- |
| `src/rlmf/error.{hpp,cpp}` | `ErrorKind`（四类）、`Error`、`Result<T>` 的跨模块数据/错误契约 |
| `src/rlmf/linalg.{hpp,cpp}` | 确定性高斯流、再正交化（列选主 QR + 两遍 MGS，含秩击穿检测）、正交性度量 |
| `src/rlmf/factorizer.{hpp,cpp}` | 算法内核：草图、幂迭代、小矩阵 SVD、数值秩截断、探针/精确/参考三类误差；`RangeSketch` 注入点 |
| `src/rlmf/factorizer_session.{hpp,cpp}` | 有状态生命周期 `Configured→InputReady→Computed→Consumed`，违例为 `StateConflict` |
| `src/rlmf/errors_explained.{hpp,cpp}` | 误差解释：把估计/精确/最优量分开陈述，附证据项 |
| `src/rlmf/fixtures.{hpp,cpp}` | 本地合成问题（精确低秩、小谱间隙、秩不足、零矩阵）与**独立全 SVD 参考**（不被测核生成） |
| `src/rlmf/logger.{hpp,cpp}` | 可重放日志：运行编号、请求参数、关键中间状态、判定理由 + CSV 索引 |
| `tools/demo.cpp` | 请求样例 CLI |
| `benchmarks/benchmark.cpp` | **独立基准**：随机法 vs 独立 `BDCSVD` 全 SVD，CSV |
| `tests/*` | 数值、合同、失败分类测试（自带微型断言框架，断言具体数值与错误类别） |

错误分类（跨模块稳定标签）：

- `InvalidArgument`：NaN/Inf、空矩阵、`k≤0`、`k>min(m,n)`、`p<0`、`q<0`、`k+p>m` 等。
- `StateConflict`：会话生命周期错位（未输入就计算、重复计算、未计算就取结果等）。
- `ResourceExhausted`：草图/SVD 阶段 `std::bad_alloc`（测试用注入草图确定性触发，不真的耗尽内存）。
- `ComputationFailed`：幂迭代基坍塌等数值失败（注入点 `RangeSketch` 可确定性复现）。

## 依赖与版本（项目内可复现，无 root、无系统安装）

- 编译器：本机 g++（C++20；在 g++ 13.3 验证通过），仅用标准库 + Eigen。
- CMake `3.30.5`（官方预编译二进制，下载解压到 `.local/cmake`）。
- Eigen `3.4.0`（纯头文件，解压到 `.local/eigen-3.4.0`）。
- 下载校验和（SHA-256）固定在 `scripts/setup.sh`：
  - CMake `f747d9b2…1dc9d`
  - Eigen `8586084f…2c1c72`

## 从干净目录复现（Linux x86_64，原生进程，无容器）

```bash
./scripts/setup.sh     # 下载并校验 CMake/Eigen 到 .local/（仅依赖下载走网络）
./scripts/build.sh     # cmake 配置 + Release 构建到 build/
./scripts/test.sh      # 构建并运行全部测试，输出可重放日志路径
./scripts/bench.sh 3   # 独立基准（默认重复 3 次），CSV 写入 build/logs/
```

等价手工命令：

```bash
.local/cmake/bin/cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
.local/cmake/bin/cmake --build build -j"$(nproc)"
.local/cmake/bin/ctest --test-dir build --output-on-failure
build/rlmf_tests --log build/rlmf-runs.log
```

## 请求样例

```bash
# 精确低秩
build/rlmf_demo --rows 120 --cols 90 --rank 5 --oversampling 10 \
                --power-iters 3 --seed 0x243f6a8885a308d3 --kind lowrank
# 秩不足
build/rlmf_demo --kind rankdef --rows 200 --cols 150 --rank 8
# 零矩阵
build/rlmf_demo --kind zero --rows 30 --cols 20 --rank 4
# 参数非法（返回退出码 2，InvalidArgument 打到 stderr）
build/rlmf_demo --rank 0
```

## 测试与日志（可重放）

`build/rlmf_tests` 覆盖 13 个用例、近百条具体断言：

- **数值**：精确低秩（全 SVD 参考，奇异值/正交性/精确残差具体阈值）、
  **小谱间隙**（σ4/σ5≈2.5%，断言显式误差界，不假装精确）、
  **秩不足**（k=8 但真秩 5，断言数值秩恰为 5）、
  **零矩阵**（合法 rank-0 结果）、**逐位可重放**。
- **合同**：会话生命周期所有错位 → 具体 `StateConflict` 代码；
  误差解释必须同时出现 `ESTIMATED / EXACT / SVD-optimal`；固定默认值。
- **失败分类**：8 种非法输入各带具体 code；注入草图确定性触发
  `ComputationFailed/sketch.failed`、`power_iteration.collapsed`、
  `ResourceExhausted/allocation.sketch`，断言**类别**而非仅“能调用”。

参考答案独立于被测核：夹具解析构造 `U_true Σ V_trueᵀ`，或用 Eigen
全 SVD 单独计算；没有任何参考值由被测随机核“自己生成”。

每条运行写一条 `RunRecord`：`run-YYYYMMDD-HHMMSS-pid-序号`、矩阵规模、
完整请求（k/p/q/seed/rank_tol）、关键中间状态（恢复奇异值、正交误差、
各残差、数值秩等）、结果与**判定理由**；另有 `.index.csv` 汇总。
用 `run_id` 中的参数即可重放同一问题。失败类别在索引 CSV 的
`error_kind/error_code` 列可直接区分。

## 未实现 / 边界说明

- 稠密 `double` 矩阵；稀疏、单精度、GPU/分布式不在范围内。
- 随机法对极小谱间隙需要很多次幂迭代才完全收敛；小间隙测试因此使用
  **显式、现实的误差界**，并在文档中如实说明，不宣称精确最优。
- 资源耗尽通过注入 `RangeSketch` 确定性测试，不在测试中真实分配超大内存。
