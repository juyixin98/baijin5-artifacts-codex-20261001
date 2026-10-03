# 加权正交 / 相似变换拟合（Weighted Orthogonal Procrustes）

从**已配对**点集 `(p_i, q_i)` 与非负权重 `w_i` 拟合最小二乘变换

```
minimize  Σ_i w_i · || s·R·p_i + t − q_i ||²
```

- `R` 为正交矩阵：**仅旋转**模式强制 `det(R)=+1`；**允许反射**模式允许 `det(R)=±1`。两种模式在配置与代码路径上完全分开。
- 刚体模式 `s=1`；相似模式从数据估计尺度 `s>0`，并带**尺度可辨识性检查**。
- 秩亏（共线/共面/重合）几何给出明确的**非唯一诊断**，与硬失败分列。
- **不做对应点搜索**：对应关系由输入行直接给定。

技术栈：C++20、CMake、Eigen（头文件库）。纯本地原生进程，无容器、无额外语言运行时。

## 模块职责

| 模块 | 位置 | 职责 |
|---|---|---|
| 数值契约 `contracts` | `src/contracts` | 输入前置条件（维度/计数/有限性/权重）、输出后置条件（`R^TR=I`、行列式、独立复算加权 RMS） |
| 算法内核 `kernel` | `src/kernel` | 加权质心、加权互协方差、SVD、行列式修正、尺度、非唯一性与尺度可辨识性诊断 |
| 误差解释 `diag` | `src/diag` | 关联 `request_id`/版本/源码位置的结构化日志；把失败原因与不确定结论分列的文本报告 |
| 应用 CLI `app` | `src/app` | CSV 数据 IO、INI 运行配置、命令行入口 |
| 独立基准 | `bench` | 独立合成数据（手写参考变换）计时与残差健全性检查 |
| 数据生成器 | `tools/gen_data` | 用三角/轴向几何**独立手写**参考变换生成夹具与真值侧车，不调用被测内核 |
| 单元测试 | `tests/unit` | 7 个可执行套件，断言具体数值、行列式、残差与失败类别 |
| 集成测试 | `tests/integration` | 夹具端到端（读 CSV + 真值侧车）与 Shell CLI 退出码/报告断言 |

## 首次使用（Linux x86_64，原生）

本机无系统 CMake/Eigen 时，脚本会把 **CMake 3.30.5 预编译版**解压到 `.toolchain/`、
把 **Eigen 3.4.0** 解压到 `third_party/`（带 SHA256 校验，无需 root）：

```bash
bash scripts/setup_deps.sh
```

配置、构建、生成样例数据、跑一次拟合：

```bash
bash scripts/run_demo.sh
```

全量验证（构建 + 9 个 CTest + Shell CLI 集成测试）：

```bash
bash scripts/run_tests.sh
```

手动分步命令：

```bash
.toolchain/cmake-3.30.5-linux-x86_64/bin/cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
.toolchain/cmake-3.30.5-linux-x86_64/bin/cmake --build build -j4
( cd build && ../.toolchain/cmake-3.30.5-linux-x86_64/bin/ctest --output-on-failure )
bash tests/integration/test_cli.sh
build/bench/bench_procrustes 3000
```

## 命令行用法

```bash
build/src/app/procrustes_fit --data data/rigid2d_pairs.csv \
  --config config/rigid2d.ini
# 也可不用配置文件，直接给开关：
build/src/app/procrustes_fit --data data/similarity2d_pairs.csv \
  --mode similarity --reflection deny --request-id my-req-1
```

CSV 每行一个配对（`#` 开头为注释，权重列可省略，省略即权重 1）：

```
p_1,...,p_d,q_1,...,q_d[,weight]
```

退出码：`0` 成功（含“非唯一但可用”）；`2` 硬失败（非法输入 / 尺度不可辨识 / 后置条件被破坏）。

运行配置见 `config/*.ini`（`mode=rigid|similarity`，`reflection=deny|allow`，容差与 `request_id`）。

## 关键数学与诊断口径

- 加权质心 `pbar = Σw·p / Σw`，中心化 `pc_i = p_i − pbar`；加权互协方差
  `H = Pc·diag(w)·Qcᵀ`，对其做 SVD：`H = UΣVᵀ`。
- 仅旋转：若 `det(VUᵀ)<0` 则令 `D=diag(1,…,1,−1)`，`R = V·D·Uᵀ`，保证 `det(R)=+1`。
- 允许反射：`D=I`，`R=V·Uᵀ`，`det(R)=±1`。
- 相似尺度（可辨识时）：`s = tr(D·Σ) / Σ_i w_i||pc_i||²`，`t = qbar − s·R·pbar`。
- **尺度可辨识条件**：源点加权展布 `α_p = Σ w_i||p_i−pbar||²` 必须显著非零；
  源点全部（有效）重合时返回 `ScaleNotIdentifiable` 硬失败。
- **旋转唯一性**：
  - 仅旋转：`rank(H) ≥ d−1` 唯一（2D 共线 rank=1 仍唯一）。
  - 允许反射：需 `rank(H)=d`；2D rank=1 时“绕目标直线镜像”是第二个精确解，
    3D rank≤1 时可绕点所在直线任意旋转 —— 均返回 `NonUniqueSolution`
    与不确定结论，但仍给出规范 SVD 最小解与真实残差。

## 独立参考与保留的验证过程

参考真值**不由被测核心生成**：`tools/gen_data/gen_data.cpp` 用手写角度矩阵
（30°、−45°、120°、90°）和独立给出的 `s,t` 直接构造目标点，并写真值侧车
`data/*_truth.txt`。测试还在 `contracts::weighted_rms` 中**独立复算残差**，
并手工构造共线情形的第二个正交解验证其同样零残差（见
`tests/unit/test_degenerate.cpp`）。

具体断言（而非“接口能调用”）覆盖：

- 2D/3D 手工旋转平移缩放：矩阵元素、平移、尺度、`det(R)`、RMS≈0；
- 反射数据在仅旋转模式必留正残差且 `det=+1`，允许反射时精确恢复镜像；
- 共线 2D：仅旋转唯一 vs 允许反射非唯一（两个精确解、行列式分别 ±1）；
- 3D 直线 rank=1：尺度 2.0 仍可辨识、旋转非唯一（45° 绕轴自旋也是解）；
- 失败类别：空集、维度/计数/权重数不匹配、负权、总权为零、非有限值、
  尺度不可辨识，均断言到具体 `Status` 与诊断关键字；
- 篡改正交性 / 行列式 / 上报 RMS 必须被契约后置条件捕获；
- 日志必须带 `request_id`、版本、`STEP/FAIL/UNCERTAIN` 与源码位置。

## 真实运行结论

最近一次在本机的完整输出已随仓保留：

- `docs/ctest_output.txt` —— `100% tests passed, 0 tests failed out of 9`
- `docs/cli_integration_output.txt` —— CLI 全部断言 `PASSED`
- `docs/benchmark_output.txt` —— 基准计时与健全性检查
- `docs/sample_report_rigid2d.txt` —— 一份完整拟合报告样例

基准（Release，每次拟合耗时，数据为独立合成）示例：2D n=4 ≈ 7µs，
3D n=1000 ≈ 68µs，所有健全性残差 ≤ 1e-14。

## 支持范围与取舍

- 支持任意 `d≥1`（测试重点覆盖 2D/3D）；权重允许为 0（该点被忽略）。
- 非唯一结果**不是错误**：返回规范最小解、真实 RMS，并在
  `uncertainties` 中显式说明；硬失败与不确定结论在日志/报告中分列。
- 容差可配置（`rank_tol`、`spread_tol`）；数值秩用相对最大奇异值阈值判定。
