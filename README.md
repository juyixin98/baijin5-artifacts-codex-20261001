# 合成对齐序列距离服务 (synthetic-distance-service)

对合成对齐 DNA 序列计算 **p 距离**与**受限替换模型校正距离**(Jukes-Cantor 1969、
Kimura 双参数 K2P)，提供按位点重采样的固定随机源自举置信区间、SQLite 运行溯源
与 FastAPI 验证接口。所有数据均为本地合成夹具，无外部服务与真实业务数据。

## 算法假设

**位点处理(缺失与模糊碱基)**
- 仅当某一列两条序列都是无歧义碱基 `A/C/G/T` 时，该位点为**有效比较位点**。
- 缺口 `-`、`N` 及全部 IUPAC 简并码(R/Y/S/W/K/M/B/D/H/V 等)一律视为缺失/模糊，
  按**成对删除**(pairwise deletion)从分子分母中同时剔除；它们本身不是输入错误。
- 全部位点均不可比较时，该序列对状态为 `undefined`,距离为 `null`(不是错误)。

**替换模型(假设分别说明)**
- `p`:原始错配比例 `p = (ti + tv) / n`,不做任何替换模型假设。
- `jc69`:假设碱基频率相等、所有替换类型速率相同。
  `d = -3/4 · ln(1 - 4p/3)`,有效域 `1 - 4p/3 > 0`(即 `p < 3/4`)。
- `k2p`:区分转换(ti)与颠换(tv)两类速率。
  `d = -1/2 · ln(1 - 2P - Q) - 1/4 · ln(1 - 2Q)`,其中 `P = ti/n`、`Q = tv/n`,
  有效域 `1 - 2P - Q > 0` 且 `1 - 2Q > 0`。

**超有效域处理**:先检查对数真数再取对数。超出有效域时该序列对状态为
`saturated`、距离为 `null`,并在 `rationale` 中记录判断理由——**绝不对负对数真数
取绝对值**、不返回伪造的正数。

**置信区间**:对 n 个有效位点有放回重抽样 B 次，每次重新估计距离；超域重复
计入 `n_saturated` 并剔除(不截断、不取绝对值),其余取百分位区间。随机源为
`numpy.random.Generator(PCG64(seed))`,种子显式传入并随结果与溯源记录保存，
同一种子结果完全可复现。算法步骤在 `app/bootstrap.py` 模块文档中逐步固定，
测试中含独立参考实现进行交叉验证。

## 模块关系

```
app/api.py         FastAPI 接口层:请求模式、错误类别 -> HTTP 状态映射
   │ 调用
app/service.py     编排:解析 -> 位点分类 -> 距离估计 -> 自举 -> 落库;幂等重放
   │ 调用                │ 调用
app/sequences.py   解析/校验(FASTA 或序列列表)   app/provenance.py  SQLite 溯源
   │ 产出 AlignedDataset                          (runs + pair_results:
app/models.py      领域算法:位点分类、p/JC69/K2P    关键中间状态与判断理由)
app/bootstrap.py   固定种子位点重抽样置信区间
app/errors.py      四类可区分错误(见下)
```

数据契约:各层之间以 `AlignedDataset` / `SiteCounts` / `DistanceEstimate` /
`BootstrapResult` 等不可变数据类传递;领域层不抛异常表达"饱和/未定义",
而是用结果状态字段表达。

**错误类别(可区分)**

| 类别 | HTTP | 含义 | 示例 |
|---|---|---|---|
| `input_validation` | 422 | 输入不合法 | 序列长度不齐、ID 重复、模型名未知 |
| `state_conflict` | 409 | 与持久化状态冲突 | 同一 `run_id` 提交不同负载 |
| `resource_exhausted` | 413 | 超出资源上限 | 序列超长、重抽样次数超限 |
| `computation_failure` | 500 | 非预期计算失败 | 未捕获异常(统一包装) |

饱和/全缺失**不是错误**,以 200 响应中逐对 `status` 字段报告。

## 溯源与诊断

每次计算写入 SQLite(默认 `distance_runs.sqlite3`,可用环境变量
`DISTANCE_DB_PATH` 覆盖):`runs` 表记录 run_id、请求哈希、完整请求、模型与种子;
`pair_results` 表记录每对序列的关键中间状态(有效位点数、匹配/转换/颠换计数、
p 距离)与判断理由(`rationale_json`)。凭 run_id 经 `GET /v1/runs/{run_id}`
可完整重放任一运行。客户端可提供 `run_id` 实现幂等:同负载重放返回已存结果,
不同负载返回 409。

## 本地验证

```bash
pip install -r requirements.txt

# 1. 单元与接口测试(63 项)
python3 -m pytest tests/ -v

# 2. 启动服务并冒烟验证
DISTANCE_DB_PATH=/tmp/runs.sqlite3 python3 -m uvicorn app.api:app --port 8000
curl -s localhost:8000/health                       # 期望 {"status":"ok"}
curl -s -X POST localhost:8000/v1/distances -H 'Content-Type: application/json' -d '{
  "sequences":[{"id":"ref","sequence":"AAAAAAAAAA"},
               {"id":"var","sequence":"GACAAAAAAA"}],
  "model":"k2p",
  "bootstrap":{"replicates":500,"confidence":0.95,"seed":7}}'
# 期望:p_distance=0.2,distance≈0.234123,status="ok",
#       bootstrap.seed=7;同一 seed 重发结果逐位一致。
```

**预期判断方式**
- `pytest` 全绿;测试断言具体数值(手算常数 JC69(0.1)=0.10732563273050497、
  K2P(0.1,0.05)=0.17018116514034703)与具体失败类别,而非仅"接口可调"。
- 手算对照:`d_JC69 = -0.75·ln(1-4p/3)`、`d_K2P = -0.5·ln(1-2P-Q)-0.25·ln(1-2Q)`。
- 饱和用例(p≥0.75 或 K2P 真数≤0)应返回 `status:"saturated"`、`distance:null`。
- 错误类别按上表对应 HTTP 状态码。

## 依赖版本

Python 3.12.3;fastapi 0.141.1、numpy 2.4.6、pydantic 2.13.5、uvicorn 0.54.0、
pytest 9.1.1、httpx 0.28.1(见 `requirements.txt`,均与开发环境实测一致)。
SQLite 使用 Python 标准库 `sqlite3`。

## 测试状态

最近一次运行:`63 passed`(2026-10-04，本仓库工作区)。无跳过、无标记为未运行的测试。
