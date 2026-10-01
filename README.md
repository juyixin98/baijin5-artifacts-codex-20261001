# CUPED & Linear Covariate Adjustment Backend

Continuous-outcome 随机实验的 **CUPED**（Deng, Xu, Kohavi & Walker, 2013）
与**线性协变量调整（ANCOVA / Lin 2013）**后端。

- 未调整（Welch/pooled 分组差）、CUPED、Lin 交互回归**并列报告**；
- 调整系数 θ 的数据来源显式声明（默认：**控制臂 × 预实验期**回归），
  从契约上排除处理后信息；
- 零方差协变量、缺失值、可疑泄漏字段都有**显式策略与失败类别**；
- 每次运行有 `run_id`、输入数据指纹、版本快照与结构化 JSON 日志；
- FastAPI + SQLite，NumPy/SciPy 手写可审计内核，无统计黑箱依赖。

---

## 1. 环境要求与安装

Python ≥ 3.10（开发环境：Python 3.12 / NumPy 2.4 / SciPy 1.15 / FastAPI 0.141）。

```bash
python3 -m venv .venv && source .venv/bin/activate   # 可选
make install          # pip install -e ".[test]"
# 或仅装运行依赖：pip install -r requirements.txt
```

所有依赖都可在本地声明、启动；输入只用仓库自带的合成数据。

## 2. 首次使用（3 条命令）

```bash
make samples          # 生成 data/sample/*.json（已随仓库提供，可复跑）
make test             # 运行全部独立测试
make demo             # 在样例上跑完整流水线并打印诊断
```

启动服务：

```bash
make serve            # uvicorn，默认 127.0.0.1:8000
# 另一个终端：
curl -s http://127.0.0.1:8000/health
curl -s -X POST http://127.0.0.1:8000/api/v1/runs \
     -H "Content-Type: application/json" \
     --data @data/sample/balanced.json | python3 -m json.tool
curl -s http://127.0.0.1:8000/api/v1/runs                 # 列表
curl -s http://127.0.0.1:8000/api/v1/runs/<run_id>        # 取回
```

配置在 `config/default.json`（数据库路径、日志、默认策略），也可在每个请求体中覆盖。

## 3. 真实运行结论

### `make demo`（`data/sample/balanced.json`，真值 τ=2.0，n=2000）

```
estimator               estimate          SE    CI95 low   CI95 high
unadjusted                2.1206      0.1417      1.8428      2.3984
CUPED                     2.0042      0.0450      1.9160      2.0924
Lin/ANCOVA                2.0028      0.0450      1.9146      2.0911

cross-checks:
  [PASS] cuped_ancova_identity        CUPED(组内θ) 与 ANCOVA τ 完全一致 (差=0)
  [PASS] standard_error_reduction     方差缩减 89.9%（SE 比 0.318）
  [PASS] variance_reduction_vs_rsquared  实际缩减 == θ 回归 R²=0.9018
  [PASS] estimator_agreement          三个估计量相差 < 0.12 SE 量级

diagnostics:
  pre_x        INCLUDED                        SMD=+0.039
  unrelated    INCLUDED                        SMD=+0.068
  post_spend   EXCLUDED(post_treatment_provenance) SMD=+0.653 LEAKAGE!
  constant     EXCLUDED(zero_variance)         SMD=+0.000
```

- 未调整估计 2.121（SE 0.142）；CUPED 2.004（SE 0.045），标准差误降约 68%、
  方差降约 90%，点估计更贴近真值 2.0；
- 泄漏字段 `post_spend`（结果后变量，由出处显式声明）默认被**剔除出调整**
  并保留证据；仅统计失衡可疑但出处声明为预实验的字段会**保留并显著告警**
  （偶发失衡不是泄漏证据）；常数列按尺度相对规则标记并剔除；
- 若强制 `leakage_policy=ignore` 把泄漏字段放进 θ，估计被机械拉偏（测试中断言
  其偏差 > 0.2），证明筛检的必要性。

### `make test`

```
94 passed in ~6–11s     # 含 5 个 mcsim 蒙特卡洛性质检验、15 个集成测试
TOTAL coverage 97%      # app/core 94–100%
```

测试日志写入 `logs/tests/run-<UTC时间戳>.jsonl`：每行含 Python/NumPy/SciPy
版本、用例 nodeid、计算步骤（θ、SE、覆盖率等）、运行时长与 PASS/FAIL 判定；
失败时带 `failure_category`，异常路径不会被当作成功。

## 4. 请求契约（节选）

```jsonc
{
  "name": "exp-001",
  "outcome_column": "y",
  "treatment_column": "treatment",      // 仅支持 0/1 两组
  "covariates": ["pre_x", "unrelated"],
  "pre_treatment_covariates": {"pre_x": true, "unrelated": true},
  "data": {"y": [..], "treatment": [..], "pre_x": [..], "unrelated": [..]},

  "theta_source": "control_pre",        // control_pre(默认) | pooled_pre | given
  "given_theta": null,                  // theta_source=given 时必填：每协变量一个系数
  "se_type": "welch",                   // welch | pooled（CUPED/分组；Lin 固定 HC1）
  "regression_interactions": true,      // Lin(2013) T×X 交互
  "missing_policy": "fail",             // fail | complete_cases | mean_impute
  "zero_variance_policy": "drop",       // drop | fail | keep
  "leakage_policy": "flag",  // flag=仅剔除出处声明的处理后字段,统计提示保留并告警 | fail | ignore
  "smd_threshold": 0.20, "alpha": 0.01, "ci_level": 0.95
}
```

错误一律为带**类型化错误码**的响应，不会返回伪成功，例如：

| HTTP | code | 触发条件 |
|---|---|---|
| 400 | `MISSING_VALUES_PRESENT` | 默认策略下存在缺失 |
| 400 | `NON_BINARY_TREATMENT` / `MISSING_ARM` / `EMPTY_DATA` | 分组设计不合法 |
| 400 | `ZERO_VARIANCE_COVARIATE` / `COLLINEAR_COVARIATES` | 退化协变量/设计矩阵 |
| 400 | `UNKNOWN_COVARIATE` / `DUPLICATE_COLUMN` | 列引用错误 |
| 422 | `LEAKAGE_DETECTED` | `fail` 策略下发现处理后字段 |
| 404 | `RUN_NOT_FOUND` | run_id 不存在 |
| 500 | `INTERNAL_ERROR` | 未预期异常（带异常类型，不吞错） |

## 5. 工程结构

```
app/
  core/
    contracts.py     # 统计契约：枚举/类型化结果/错误码/Settings
    data.py          # 校验、缺失与零方差策略（行/列丢弃全部计数上报）
    ols.py           # OLS 原语：正规方程 + HC0/HC1 三明治方差 + 秩检查
    estimators.py    # 未调整分组差 / CUPED(θ来源) / ANCOVA / Lin
    diagnostics.py   # SMD 平衡、泄漏筛检、CUPED–ANCOVA 恒等式等交叉核验
    service.py       # 编排：run_id、数据指纹、版本、并列结果
  api/
    schemas.py       # Pydantic 请求/响应契约
    storage.py       # SQLite 持久化（关系投影 + 规范 JSON 全量）
    logging_config.py# JSON 结构化日志（run_id / fingerprint / versions）
    main.py          # FastAPI、类型化错误处理
  synth.py           # 确定性合成数据生成器（已知真值/失衡/泄漏/缺失/常数）
scripts/
  generate_sample.py # 生成 data/sample
  run_demo.py        # 命令行演示
config/default.json  # 启动与默认策略配置
tests/
  unit/              # 校验、内核(QR独立参考)、诊断、冻结回归、MC性质、生成器
  integration/       # FastAPI + SQLite 端到端（TestClient）
data/sample/         # balanced / imbalanced / missing + *.truth.json
```

## 6. 统计要点（详见 [docs/STATISTICS.md](docs/STATISTICS.md)）

- **θ 来源**：默认在**控制臂、仅用预实验协变量**上拟合 y ~ 1+X，
  保证调整不干扰随机化、ATE 无偏；`pooled_pre` 给出组内（ANCOVA）斜率；
  `given` 接受外部系数。来源写入结果与日志。
- **SE 与分组设计一致**：CUPED/未调整用每臂方差的 Welch（或 pooled）SE；
  回归用 HC1（或 HC0/classical）。不允许跨族混用（混用返回 `CONFIG_ERROR`）。
- **交叉核验**：CUPED(组内θ) ≡ 主效应 ANCOVA；控制臂样本内方差缩减 ≡ R²；
  SE 缩减；三估计量一致性。每条给具体数值与 PASS/FAIL。
- **独立测试答案**：测试内有一套基于 **QR 分解与标量公式**的独立参考实现
  （不同于生产内核的正规方程路径）；另有手算 8 行数据集的精确常数和
  冻结样例答案；蒙特卡洛检验覆盖偏差、SE 校准、95% CI 覆盖、
  无相关协变量、30/70 失衡与泄漏字段偏倚。

## 7. 常用命令

```bash
make test        # 全部测试（含蒙特卡洛）
make test-fast   # 跳过 mcsim 的快速集
make test-cov    # 覆盖率
make samples     # 重新生成样例数据（种子固定，可复现）
make demo        # 命令行完整报告
make serve       # 启动 API
```
