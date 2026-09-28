# AIPW 估计后端（Cross-fitted Augmented IPW）

对二元处理、连续结果的合成数据，估计平均处理效应（ATE，另有 ATT），采用
K 折交叉拟合（cross-fitting / DML 风格）的增广逆概率加权（AIPW）估计量，
并基于影响函数给出方差、置信区间；支持聚类独立单元的稳健推断。
所有数据均为本地合成夹具，无外部账号与真实业务数据。

## 技术栈与安装

Python 3.12，固定版本见 `requirements.txt`：

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

仅依赖 numpy / scipy / fastapi / pydantic / uvicorn；**不依赖 scikit-learn**，
所有倾向模型（IRLS 逻辑回归）与结果模型（标准化 OLS/岭）均在
`src/aipw/models.py` 中直接实现，训练-验证边界完全显式可审计。

## 模块划分（按统计契约 / 估计内核 / 证据与诊断 / 复现实验拆分）

| 模块 | 职责 |
|---|---|
| `src/aipw/contract.py` | 统计契约：配置、数据校验、结果对象、错误四分类、双重稳健条件文本 |
| `src/aipw/models.py` | 倾向（逻辑回归 IRLS）、结果（标准化 OLS）、**仅训练折拟合**的标准化器 |
| `src/aipw/crossfit.py` | 分层 K 折、**簇级 K 折**、折外预测严格行对齐、每折标准化统计留痕 |
| `src/aipw/estimators.py` | g-computation、IPW、HT-AIPW、增广 Hájek（稳定权重）、ATT 内核 |
| `src/aipw/influence.py` | 影响函数方差：iid 与聚类三明治；独立单元数；t/正态临界值 |
| `src/aipw/diagnostics.py` | 独立证据：标准化泄漏精确比对、OOF 独立重拟合、分量一致性 |
| `src/aipw/simulation.py` | 四种已知真值合成 DGP（含仅一模型正确、双错）与聚类 DGP |
| `src/aipw/jobs.py` | SQLite 运行注册表、状态机、可重放事件日志 |
| `src/aipw/pipeline.py` | 组合根：校验→分折→交叉拟合→估计→推断→诊断结果 |
| `src/aipw/api.py` | FastAPI：`/estimate`、`/runs/{id}`、`/runs/{id}/events`、`/verify` |
| `tests/oracle.py` | **独立参考实现**（scipy L-BFGS-B + lstsq + 显式循环），不 import 被测内核 |
| `scripts/reproduce.py` | 一键复现/验证脚本，输出带编号的检查与 JSON 报告 |

## 快速开始

```bash
# 测试（断言具体数值与失败类别，而非“接口能调用”）
python3 -m pytest tests/ -p no:warnings

# 覆盖率
python3 -m pytest tests/ -p no:warnings --cov=src/aipw --cov-report=term-missing

# 复现实验（蒙特卡洛 + 独立实现对账 + 泄漏/错配检测 + 错误分类）
python3 scripts/reproduce.py --trials 15 --n 3000

# 启动服务（SQLite 持久化运行日志）
AIPW_DB=runs.db uvicorn aipw.api:app --port 8000
```

请求示例：

```json
POST /estimate
{
  "data": {"x": [[...]], "a": [0,1,...], "y": [..], "cluster_id": [0,0,...]},
  "config": {"folds": 5, "trim_propensity": {"lower": 0.01, "upper": 0.99}},
  "seed": 2024,
  "run_id": "run-0001"
}
```

`cluster_id` 出现即自动启用聚类推断并使用簇级分折（详见
[docs/STATISTICAL_CONTRACT.md](docs/STATISTICAL_CONTRACT.md)）。

## 错误四分类（可区分，HTTP 状态码不同）

| category | 含义 | HTTP |
|---|---|---|
| `input_error` | 输入形状/类型/语义非法（非二元处理、行数不一致、非有限值等） | 400 |
| `state_conflict` | run id 复用、非法状态转移、折编号/OOF 错配、标准化泄漏 | 409 |
| `resource_exhausted` | 行数/矩阵单元预算超限、内存耗尽 | 507 |
| `computation_failed` | IRLS 不收敛、奇异设计、倾向越界等数值失败 | 422 |

## 运行编号与可重放日志

每个 run 在 SQLite 中按 `pending → running → succeeded/failed` 记录有序事件：
种子（重放折划分）、n/p/folds、每折大小、点估计/SE/CI、三分量估计值、
失败类别与细节。`POST /runs/{id}/verify` 用存储的种子确定性重放，要求点估计
位级复现、OOF 与独立重拟合一致、标准化统计仅来自训练折。

## 边界语义（重要）

- **双重稳健是条件成立，不是任意错设都正确**：倾向模型或两个结果回归中
  至少一组正确（叠加正定性、iid/簇独立抽样），AIPW 才一致且影响函数方差
  有效；两者皆错时估计有偏，CI 不保证覆盖。复现实验中 `both_wrong` 夹具
  实测偏差 ~0.86、覆盖率 0.0，即用于证伪“双错仍正确”。
- **倾向截断会改变目标总体**（朝重叠总体偏移）；截断比例显式报告
  `trimmed_fraction`，不静默处理。
- **交叉拟合折外预测逐行对齐**：`mu0[i],mu1[i],e[i]` 来自从未见过行 i 的
  模型；折划分是精确划分，越界/重复/错配以 `state_conflict` 失败。
- **每折标准化只用训练行**；诊断以 1e-10 容差逐折逐臂比对留痕统计量，并额外
  验证“全量拟合”替代项确实不同（保证检测有功效，而非空转通过）。
- **聚类推断的独立单元是簇**：分折在簇层面进行（同簇成员不跨训练/验证），
  方差为 `G·s²_C/n²`，CI 使用 t(G−1)。
- **稳定权重路径是增广 Hájek 比率估计量**，与 HT 形式是不同的有限样本加权，
  配置中显式选择，结果中以 `method` 标明。
- 结果中的 `insample_gcomp_point` 仅是**泄漏对照量**，永远不作为答案。

## 未执行/不适用的检查（不写成已通过）

- 未做真实数据库/多进程并发压测：SQLite 以单连接 + 线程锁保护，覆盖了
  FastAPI 线程模型，但未在多 worker 部署下验证。
- 未做身份认证/鉴权、速率限制：本地合成数据后端，边界假设见
  [docs/STATISTICAL_CONTRACT.md](docs/STATISTICAL_CONTRACT.md)。
- ATT 路径当前不提供 gcomp/IPW 对照分量（响应中为 `null`）；其 DR 条件与
  ATE 类比，未做独立蒙特卡洛覆盖实验。
