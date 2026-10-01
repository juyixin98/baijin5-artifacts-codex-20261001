# CUPED 及线性协变量调整后端

连续结果随机实验的 CUPED / 线性协变量调整服务。Python + FastAPI + NumPy +
SciPy + SQLite，从空工程搭建，只依赖可本地安装的声明式依赖与合成数据。

## 它保证什么

1. **预实验协变量硬门槛**：每个协变量必须在注册时声明 `pre_treatment`；
   标记为 `False` 的处理后字段（泄漏字段）在估计前以
   `LEAKED_COVARIATE` 错误类别拒绝，永不进入调整。
2. **θ 系数来源明确**：支持两种来源并在结果中记录
   （`theta.source / n_units_used / n_arms_used`）：
   - `control`：经典 CUPED，仅在对照组 T=0 上拟合 θ，处理组的处理后结果
     完全不参与系数（有单元测试通过扰动处理组结果证明不变性）；
   - `pooled_fwl`：在 `Y ~ 1 + T + X` 的合并 OLS 中取 X 系数（Frisch–
     Waugh–Lovell），此时调整后均值差严格等于 OLS 的 T 系数（内核有一致性
     校验，测试独立断言）。
3. **缺失值与零方差显式策略**：
   - 缺失：`error`（报错 `COVARIATE_VALUE_MISSING`）或 `mean_impute`
     （池均值插补，逐列记录缺失/插补计数）；
   - 零方差：`error`（`ZERO_VARIANCE_COVARIATE`）或 `drop`（剔除常数列
     并告警）；共线列触发 `RANK_DEFICIENT_DESIGN`。
4. **标准误与分组设计一致**：调整前后都使用同一套两独立组 Welch 公式
   `s1²/n1 + s0²/n0`（与随机化分组一致），因此未调整/调整结果可直接比较，
   并排报告。
5. **未调整与调整并列报告**：同一 run 同时返回 `unadjusted` 与 `adjusted`
   估计量、SE、t、p、CI、组内均值/方差，以及 `se_reduction /
   variance_reduction`。
6. **独立参考回归**：`app/core/reference.py` 从头实现 OLS（model SE 与 HC1
   三明治 SE），**不被内核用于产出头条数字**；每次运行附带
   `Y ~ 1 + T + X` 的处理系数作为交叉验证；测试中还独立第三次推导公式
   进行核验（参考答案不是被测核心自己生成的）。
7. **失败不伪装成功**：校验/数值失败都有稳定错误码（见
   `app/core/errors.py`），通过 HTTP 映射到 404/409/422；失败运行以
   `status=failed` 持久化，永远不能以成功结果取回。

## 工程结构

```
b/
├── app/
│   ├── core/          # 统计契约、估计内核、校验、参考回归、合成数据
│   │   ├── contracts.py     # 不可变领域类型（统计契约）
│   │   ├── errors.py        # 错误分类法（16 个稳定错误码）
│   │   ├── estimator.py     # Welch DiM、CUPED θ、调整与诊断
│   │   ├── reference.py     # 独立 OLS + HC1（交叉验证用）
│   │   ├── validation.py    # 系统边界校验与缺失/零方差策略
│   │   ├── synthetic.py     # 已知真值的合成实验生成器
│   │   ├── config.py        # YAML + 环境变量配置
│   │   └── logging_setup.py # 关联 run_id / 版本的结构化日志
│   ├── storage/       # SQLite 持久化与服务编排
│   │   ├── db.py
│   │   └── service.py
│   └── api/           # Pydantic 契约 + 薄路由
│       ├── schemas.py
│       └── app.py
├── scripts/           # 复现实验、样例生成、服务启动、加载客户端
├── data/              # 样例数据（CSV/JSONL + 真值元数据）
├── config/default.yaml
└── tests/             # 独立单元测试 + 集成测试
    ├── unit/          # 25 个单元测试
    └── integration/   # 14 个存储/HTTP 端到端测试
```

## 快速开始

```bash
# 1) 安装依赖（环境已满足时可跳过）
python3 -m pip install -r requirements.txt

# 2) 生成样例数据（已知真实效应 2.0、协变量系数 3.0）
python3 scripts/generate_sample_data.py

# 3) 跑独立复现实验（5 个场景，输出每步计算与 PASS/FAIL 判定）
python3 scripts/run_reproducibility.py
```

预期输出（节选自真实运行，完整文件见 `logs/reproducibility_output.txt`）：

```
[1/5] balanced + correlated covariate
  unadjusted: est=2.0073 se=0.0999
  adjusted:   est=2.0076 se=0.0318
  theta={'x_pre': 2.9982, 'x_irrelevant': 0.0327} (source=control, n_used=2024)
  variance_reduction=0.8990 reference OLS beta_T=2.0074 se_hc1=0.0318 R2=0.9084
  -> PASS
[3/5] covariate imbalance realization
  realized |SMD|=0.315
  unadjusted est=1.8808 (error 0.8808)
  adjusted   est=0.9799 (error 0.0201)
  -> PASS
[4/5] leakage field rejection
  rejected with code=LEAKED_COVARIATE
  -> PASS

7/7 checks passed
```

### 启动 HTTP 服务

```bash
scripts/run_server.sh                      # 默认 127.0.0.1:8000
PORT=8011 scripts/run_server.sh            # 自定义端口
```

另一个终端：

```bash
# 注册实验（显式声明协变量是否为预实验变量）
curl -X POST http://127.0.0.1:8000/experiments -H 'Content-Type: application/json' -d '{
  "experiment_id": "demo",
  "covariates": [
    {"name": "x_pre", "pre_treatment": true},
    {"name": "x_irrelevant", "pre_treatment": true}
  ]
}'

# 上传观测并运行（或直接用样例加载器）
python3 scripts/load_sample.py data/sample_balanced.jsonl
```

### HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查与版本 |
| POST | `/experiments` | 注册实验与协变量声明 |
| POST | `/experiments/{id}/observations` | 批量上传观测 |
| POST | `/experiments/{id}/runs` | 执行估计（可选 θ 来源与缺失/零方差策略） |
| GET | `/runs/{run_id}` | 取某次运行（成功或失败） |
| GET | `/experiments/{id}/runs` | 列出实验下所有运行 |

`POST .../runs` 请求体全部可选：

```json
{
  "covariate_names": ["x_pre"],
  "missing_strategy": "mean_impute",
  "zero_variance_strategy": "drop",
  "theta_source": "control"
}
```

## 测试

```bash
python3 -m pytest tests/                 # 全部 39 个测试
python3 -m pytest tests/unit -q          # 仅单元测试
python3 -m pytest tests/integration -q   # 仅集成测试
```

测试断言**具体统计结果与具体失败类别**，而非"接口能调用"：

- 已知效应恢复（调整后误差 < 0.15）、θ 恢复 β≈3.0、理论方差降幅 > 0.80；
- 无相关协变量时方差降幅 ≈ 0；组间失衡时调整把误差从 0.88 降到 0.02；
- 泄漏字段 → `LEAKED_COVARIATE`；缺失/零方差/共线/空组/重复 id/非二元
  处理/缺失结果各有独立错误码断言；
- pooled FWL 下调整估计与独立 OLS 处理系数严格一致；
- 失败运行在 SQLite 中为 `status=failed`，HTTP 为 422/404/409。

未调整估计量的 95% 置信区间还在 300 个独立种子上验证覆盖率（允许蒙特
卡罗误差）。

## 日志与可追溯性

每次运行的所有日志行带 `run=<run_id>`，并记录 app/numpy/scipy 版本、
Python 版本、平台、分组样本量、θ 拟合来源、未调整/调整估计步骤、方差
变化判定。复现脚本与 HTTP 运行写入 `logs/cuped.log`；pytest 写
`logs/pytest.log`。失败时记录错误码与详情，不吞异常。

## 统计方法备注

- **为什么对照组 θ**：经典 CUPED 选择仅用处理前（对照组）信息估计系数，
  避免处理组结果（含处理效应）污染协方差系数；本实现通过显式行计数与
  扰动不变性测试保证这一点。
- **为什么还用 Welch 而不是模型 SE 作为头条**：随机化推断的分组设计是
  两个独立组，调整前后沿用同一公式使 SE 可比；同时报告 OLS 的 HC1 稳健
  SE 作为模型视角交叉验证，二者数值高度一致。
