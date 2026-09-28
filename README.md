# AIPW 估计后端（合成二元处理数据）

对合成二元处理数据做交叉拟合 **AIPW（Augmented Inverse Probability Weighting）**
因果效应估计的后端。技术栈：Python 3.12 / NumPy / SciPy（仅作可用依赖，核心
公式为手写）/ FastAPI / SQLite。所有数据来自本地合成 DGP，无任何生产账号或真实
业务数据。

## 模块划分（四类真实模块，非单文件脚本）

| 关注点 | 模块 |
| --- | --- |
| 统计契约 | `aipw_backend/contract.py`（形状/二元/有限性/支撑/聚类设计）、`config.py`、`errors.py`、`folds.py`、`scaling.py`、`models.py` |
| 估计内核 | `aipw_backend/kernel.py`（折外交叉拟合、AIPW/ATT、影响函数方差、聚类方差） |
| 证据与诊断 | `aipw_backend/diagnostics.py`（重叠、逐折预测、影响函数尾部、校正项、已知效应判定）、`repository.py`（SQLite 运行登记）、`service.py`（生命周期 + 结构化日志） |
| 复现实验 | `aipw_backend/dgp.py`（合成数据与**真实** nuisance 函数）、`reference.py`（**独立**参考估计）、`experiment.py` |
| HTTP 边界 | `aipw_backend/api.py` |
| 独立配置 | `configs/default.json`、`configs/experiment.json` |
| 测试 | `tests/`（73 个，独立断言具体数值与失败类别） |
| 脚本 | `scripts/run_experiment.py`、`scripts/replay_run.py`、`scripts/serve.py`、`verify.sh` |

## 快速开始

```bash
pip install -r requirements.txt       # 直接固定版本
# 或 pip install -r requirements-lock.txt  # 含全部传递依赖的完整锁
python3 -m pytest tests/ -q          # 73 passed
bash verify.sh                       # 环境检查 + 测试 + 完整复现实验 + API 冒烟
```

单独运行复现实验并落盘（每个重复都有可重放的 `run_id`）：

```bash
python3 scripts/run_experiment.py configs/experiment.json \
    artifacts/experiment_results.json artifacts/runs.db
python3 scripts/replay_run.py artifacts/runs.db <run_id>
```

启动 API：`python3 scripts/serve.py`，然后 `POST /api/runs`
（字段 `x`、`a`、`y`、可选 `cluster`、`known_effect`、`run_id`）。

## 估计量与公式

ATE 的逐样本影响函数（e 为倾向得分，m1/m0 为两组结果回归，全部为折外预测）：

```
psi_i = m1(X_i) - m0(X_i)
        + A_i/e_i       * (Y_i - m1(X_i))
        - (1-A_i)/(1-e_i) * (Y_i - m0(X_i))
tau_ATE = mean_i psi_i
```

- **折外对应（验收规则 1）**：样本 i 的三个 nuisance 预测只来自“训练时未见过 i”
  的那一折模型；折内训练/验证索引显式保存并有三重结构校验（不交、并集为全样本、
  valid[k] 恰为折号等于 k 的行）。内核写预测槽时有“仅写一次”断言，重复折/漏折
  分别报 `computation_failure`。
- **每折标准化只拟合训练部分（规则 2）**：`fit_scaler(x[train_idx[k]])`，再原样
  作用于验证折；从不使用全行均值/方差。常量列除以 1 而非 0。
- **方差与独立单元（规则 3）**：iid 下独立单元是个人，`se = sd(psi)/sqrt(n)`；
  有 `cluster` 时独立单元是群，先在群内对影响函数求和 S_g，再用经典聚类稳健
  分母 G/(G-1) 计算。交叉拟合在聚类设计下**整群留折**，一群不会跨折。
- ATT 使用只依赖 e 与 m0 的双稳健得分，见 `kernel.py` 文档字符串。

## 双重稳健成立条件（规则 4，不夸大）

ATE 一致的充分条件（标准正则条件 + 支撑/正定性下）：

1. 倾向模型 e(X) 一致，**或**
2. 两个结果回归 m1(X)、m0(X) 都一致；

并且：条件可交换性（无未测混杂）、概率处理满足正定性
`0 < e(X) < 1`、抽样独立单元明确。交叉拟合使 nuisance 的一阶估计误差不影响
渐近分布。

**若倾向和两个结果模型同时错设，一般不保证无偏。** 测试中的 `both_wrong`
夹具（常数倾向 + 常数结果）在本混杂 DGP 下稳定偏约 +0.4，95% 区间覆盖率塌到
0，这是被显式断言的事实，而不是被隐藏。

### 一个已编码的边界现象（保守而非反保守）

`propensity_only`（倾向 logistic 正确、结果模型为常数）场景下，倾向得分 MLE
满足样本内协变量平衡 `Σ(A−ê)X=0`，而本 DGP 的真实结果回归恰在同一 X 线性空间
中，估计的 ê 隐式投影掉了该部分变异（Hájek / 倾向投影的效率增益）。此时
**插件影响函数 SE 大于估计量实际抽样 SD（比率约 1.5–2.3），覆盖率≥名义水平，
属保守**；`both_correct` 与 `outcome_only` 的 SE 比率约为 1。实验与测试按场景
分别断言，不把“任意错设都精确”当作成立。

### 聚类设计的两种支持形态

- **群内个体随机化**（每群含两臂）：共同群冲击在 AIPW 处理/对照两项中群内相消，
  SE 通常不膨胀——这是正确的抵消，代码与测试保留该事实；
- **整群随机化**（一群同属一臂，DGP `cluster_randomized=True`）：共同冲击不
  相消，忽略聚类会严重低估。复现实验即此设计：150 群、ICC 0.6，聚类感知覆盖约
  0.95，行独立覆盖约 0.5。

## 错误分类（四类可区分）

| 类别 | 触发示例 | HTTP |
| --- | --- | --- |
| `input_error` | 维度不符、非二元 A、NaN、长度不对齐、非法聚类设计 | 400 |
| `state_conflict` | `run_id` 重复、未开始先记结果、终态再转移 | 409 |
| `resource_exhausted` | `n*p` 超 `max_feature_cells` 预算 | 413 |
| `computation_failure` | 倾向触边界（正定性被违反）、牛顿不收敛、非有限值、折预测碰撞/遗漏 | 422 |

## 测试日志与可重放性

每次运行经 `RunService` 落 SQLite（`queued→running→succeeded/failed`），日志带
唯一 `run_id`、级别标记（`INPUT_ACCEPTED`、`CROSSFIT_DONE`、`RUN_SUCCEEDED` 及
四类失败标记）、关键中间状态（折样本数、倾向极值、估计、SE、独立单元）与判定
理由（`ci_covers_known`、`z_vs_known`）。用 `replay_run.py` 可按编号完整重放。

## 合成夹具与答案独立性

`dgp.py` 同时提供真实 `e(X)`、`mu_a(X)`；`reference.py` 用它们独立实现 g-formula、
IPW、oracle AIPW 与未调整均值差，**不导入被测内核**。测试用这些独立答案核验
公式，并额外独立重训折模型逐行比对折外预测，检测训练/验证泄漏与折编号错配。

## 边界语义与“未执行的检查”（不写成已通过）

- 仅支持二元处理、ATE/ATT、本包内置的两类 nuisance 模型（外加显式错设探针）。
- `propensity_trim` 默认 0；设为 >0 时做对称截断并在证据中记录，截断本身会引入
  有限样本偏差，不宣称无代价。
- 测试默认复现规模较小（60–150 重复）；`configs/experiment.json` 的 400 重复
  由 `verify.sh`/实验脚本执行，不在每次 `pytest` 内强制运行。
- 未执行（环境未提供相应工具或无网络）：bandit 静态扫描、mypy/pyright 类型
  检查、black/ruff 格式校验、依赖的 CVE 在线比对、多进程/高并发压力测试。
  这些不应被视为已通过；代码遵循了 PEP8/类型注解/无硬编码密钥的相应约束。
- SciPy 已固定版本但核心数学未依赖其估计流程；若移除 SciPy 不影响公式正确性。
