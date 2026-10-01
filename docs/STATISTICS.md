# 统计契约与内核说明（STATISTICS）

本文档固定后端的统计含义，使每一个返回字段都可追溯、可复核。

## 1. 设定

- 连续结果 \(Y_i\)，二元分组 \(T_i\in\{0,1\}\)（0=对照，1=处理），独立样本。
- 预实验协变量向量 \(X_i\)（处理分配**之前**测量），维度 k。
- 目标：平均处理效应（ATE）\(\tau = E[Y(1)-Y(0)]\)。

当前后端仅支持两组；多臂设计在校验层以 `NON_BINARY_TREATMENT` 拒绝。

## 2. 三个并列估计量

### 2.1 未调整分组差

\[
\hat\tau_{\text{unadj}}=\bar Y_1-\bar Y_0,\qquad
SE_{\text{Welch}}=\sqrt{s_1^2/n_1+s_0^2/n_0}
\]

自由度用 Welch–Satterthwaite；`se_type=pooled` 时用合并方差与 \(n_1+n_0-2\)
自由度。两种 SE 与"独立两组"分组设计严格一致。

### 2.2 CUPED（Deng et al. 2013）

调整后结果：

\[
\tilde Y_i=Y_i-(X_i-\bar X)^\top\theta,\qquad
\hat\tau_{\text{CUPED}}=\overline{\tilde Y}_1-\overline{\tilde Y}_0
\]

在"线性、加性"真值下，以总体协方差表示的最优系数

\[
\theta^*=\operatorname{Cov}(X,Y)/\operatorname{Var(X)}
\]

使 \(\operatorname{Var}(\tilde Y)\) 最小。**θ 来源（`theta_source`）显式声明：**

| 来源 | 拟合数据 | 用途 |
|---|---|---|
| `control_pre`（默认） | 仅控制臂、仅预实验 X：\(Y\sim 1+X\) | Deng 等推荐；从设计上不接触处理臂、不含处理后信息 |
| `pooled_pre` | 全样本 \(Y\sim 1+T+X\)，取 X 斜率 | 组内（ANCOVA）斜率；对 T 正交 |
| `given` | 调用方提供 | 外部/历史 θ（长度必须等于 k，非有限值报 `CONFIG_ERROR`） |

CUPED 的 SE 在**调整后结果**上按臂计算 Welch/pooled，与未调整同属分组族。
CUPED 不接受 `hc1/classical`（回归族 SE），反之 Lin 回归不接受 `welch`，
跨族请求返回 `CONFIG_ERROR`，避免静默换公式。

### 2.3 ANCOVA 与 Lin（2013）

协变量在**合并样本均值**处中心化 \(X_c=X-\bar X\)：

- 主效应 ANCOVA：\(Y=\alpha+\tau T+\gamma^\top X_c+\varepsilon\)
- Lin 估计量（默认）：再加入 \(T\cdot X_c\) 交互项。在完全随机实验下，
  Lin 估计量对处理效应方差是"回归调整友好"的（model-robust），配合
  **HC1 异方差稳健三明治 SE**：

\[
\widehat{\operatorname{Var}}(\hat\beta)=\frac{n}{n-p}(X^\top X)^{-1}
X^\top\operatorname{diag}(e_i^2)X(X^\top X)^{-1}
\]

另提供 `hc0`（无小样本修正）与 `classical`（\(\hat\sigma^2(X^\top X)^{-1}\)）。
设计矩阵秩亏时返回 `COLLINEAR_COVARIATES`。

## 3. CUPED–ANCOVA 恒等式（交叉核验）

用**组内斜率** \(\hat\theta_w\)（等价于在 `[1,T,X]` 上回归取 X 系数）时：

\[
\hat\tau_{\text{ANCOVA}}
=(\bar Y_1-\bar Y_0)-(\bar X_1-\bar X_0)^\top\hat\theta_w
=\hat\tau_{\text{CUPED(pooled\_pre)}}
\]

后端同时拟合 pooled θ 的 CUPED 与主效应 ANCOVA，断言二者数值相等
（容差 1e-8 相对误差）。这是对两条独立代码路径的代数级核验。

控制臂 θ 样本内还有一个精确恒等式：带截距 OLS 的

\[
R^2_\theta=1-\frac{\sum_i(Y_i-\hat Y_i)^2}{\sum_i(Y_i-\bar Y_0)^2}
\]

故控制臂调整后方差缩减比例必须等于 \(R^2_\theta\)（测试中误差为 0）。
该恒等式**仅在 θ 于控制臂拟合时成立**；`pooled_pre` 时对应的参考量是
合并组内残差而非控制臂方差，因此后端只在 `control_pre` 下执行这条核验，
不套用不成立的恒等式。`given`（外部 θ）下两条方差缩减核验都省略而非伪造。

`standard_error_reduction` 是**信息性**指标而非硬性恒等式：预实验协变量
渐近上应降低 SE，但在极小样本中一个真基线但无关的协变量可能因偶然而抬高
SE（n=20 模拟中比值可达约 1.3）。它如实记录比值，不产生误报的 FAIL。

## 4. 手算核验数据集（`tests/conftest.hand_dataset`）

8 行，\(Y=1+2T+3X+\varepsilon\)，对照 X=1..4、处理 X=5..8
（**人为构造**基线失衡），固定残差
\(\varepsilon=(0.10,-0.10,0.05,-0.05\mid -0.20,0.20,-0.15,0.15)\)。

逐项手算：

- 对照均值 8.5，处理均值 22.5 → 未调整差 **14.0**（真值仅 2.0，体现失衡危害）；
  Welch SE = √7.6125 ≈ 2.7591。
- 控制臂斜率：Sxx=5, Sxy=14.85 → θ=**2.97**，截距 1.075；
  控制 RSS=0.0205、σ²=0.01025，SE(θ)=√(0.01025/5)≈0.04528。
- 组内斜率：Sxx_within=10, Sxy_within=30.2 → θ_w=**3.02**。
- 组均值差 \(\bar X_1-\bar X_0=4\)：
  - CUPED(控制 θ)：14 − 4×2.97 = **2.12**，SE=√0.01425≈0.11937；
  - ANCOVA / Lin：14 − 4×3.02 = **1.92**。
- 给定 θ=3（结构斜率）：14 − 12 = **2.0**，精确恢复真值。

这些常数在 `tests/unit/test_estimators.py` 中被**精确断言**，并与基于
QR 分解的独立参考实现（`tests/conftest.py`）相互比对。

## 5. 协变量策略

### 5.1 预实验属性与泄漏筛检

请求以 `pre_treatment_covariates` 显式声明每个协变量是否在处理前测量。

1. **硬信号（权威出处）**：声明为 `false` → 判定为处理后字段，
   `provenance_post_treatment=true`。
2. **统计提示（非出处）**：即便声明为预实验，若臂间标准化均值差
   \(|SMD|>阈值\) 且 Welch 平衡检验 \(p<\alpha\)，标记可疑
   （`leakage_flag=true` 但 `provenance_post_treatment=false`）。SMD 只说明
   偏离，联合显著性降低"纯随机失衡"的可能性；但真基线变量偶发失衡是正常的，
   因此这只是提示、不是泄漏证据。

`leakage_policy`：

- `flag`（默认）：**仅**把权威出处的处理后字段**剔除出调整**并告警；
  纯统计提示的字段**保留在模型中**，同时给出显著告警请人工核查测量时点；
- `fail`：出现任一类标记都以 `LEAKAGE_DETECTED` 拒绝整个运行；
- `ignore`：调用方明知故用（诊断仍保留）。

这样默认行为不会因为一次偶发失衡就静默改写拟合模型，同时保证已知处理后信息
绝不进入默认估计。测试证明：把 \(Z=Y+\text{噪声}\) 这类处理后字段在
`ignore` 下强塞入会把效应显著拉向 0。

SMD 定义（合并 SD）：

\[
SMD=\frac{\bar X_1-\bar X_0}
{\sqrt{\big((n_1-1)s_1^2+(n_0-1)s_0^2\big)/(n_1+n_0-2)}}
\]

### 5.2 零方差协变量

判定是**尺度相对**的：当样本标准差 \(\le 10^{-12}\max(1,|\bar x|)\) 视为常量
（全缺失列也算无 spread 信息）。这样既不会误删 1e-10 量级但确实变化的列，
也不会把 1e9 量级上近似常量的列放进 OLS 导致数值爆炸。

`drop`（默认，标记并剔除）/ `fail`（`ZERO_VARIANCE_COVARIATE`，details 给
观测数）/ `keep`。结果**零方差**或无观测始终致命
（`ZERO_VARIANCE_OUTCOME`）。每臂在准备后须至少 2 个观测，否则
`INSUFFICIENT_SAMPLE`（方差/Welch SE 无定义）。

### 5.3 缺失值

结果或协变量中的 `null`：

- `fail`（默认）：`MISSING_VALUES_PRESENT`，details 给缺失行数；
- `complete_cases`：行删除，报告删除行数；
- `mean_impute`：用该列观测均值填充（结果列同理），报告填充行数。

所有删除/填充计数都写入结果，绝不静默发生。

## 6. 蒙特卡洛性质检验结论（`tests/unit/test_mc_properties.py`）

固定 DGP \(Y=0.5+\tau T+\beta X+\varepsilon\)，数百次重复：

| 检验 | 断言 |
|---|---|
| 无偏性与 SE 校准 | \(\beta=2,\sigma_\varepsilon=1\)：\|\|偏差\|<0.04；解析 SE/经验 SD ∈ (0.92,1.08)；方差比 ∈ (0.15,0.26)（理论 ρ²=0.8 → 0.2） |
| 95% CI 覆盖 | 未调整与 CUPED 经验覆盖都 ∈ [0.92,0.98] |
| 无相关协变量 | β=0：方差比 ∈ (0.90,1.12) 且仍无偏 |
| 30/70 失衡 | CUPED 偏差 <0.06 且 SD 小于未调整 1/2.5 |
| 泄漏字段 | 强制纳入 \(Z=Y+\eta\)：估计偏差 >0.2 且向 0 收缩 |

## 7. 可复现身份与日志

- `run_id`：每次运行随机 12 位十六进制；
- `data_fingerprint`：对分析输入（列名、预实验声明、数据）**及生效的全部
  分析选择**（θ 来源、外部给定 θ、SE 族、交互项、缺失/零方差/泄漏策略、
  SMD 阈值、α、置信水平）的规范 JSON 取 SHA-256 前 16 位；任一改变数值
  答案的因素变化都会改变指纹；
- `versions`：Python/NumPy/SciPy/后端版本；
- `config_snapshot`：本次生效的全部策略；
- 服务日志为 JSON（stderr + `logs/cuped.log`），测试日志为
  `logs/tests/run-<ts>.jsonl`，均带 run_id / fingerprint / nodeid 可关联。
