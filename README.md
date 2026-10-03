# dtw-service

两个合成特征序列的动态时间规整（DTW）：返回规整路径、总代价与归一代价、局部伸缩率。
技术栈：Python 3.12 / FastAPI / NumPy / SciPy。所有输入均为调用方提供的合成序列，无外部账号与真实业务数据。

## 算法假设（固定契约）

| 项 | 约定 |
|---|---|
| 局部距离 | 欧氏距离（`metric: euclidean`，固定） |
| 步型 | 对称步型 {(1,1), (1,0), (0,1)}，下标单调不减 |
| 斜率约束 | 同一方向连续非对角步 ≤ `max_consecutive_run`（默认 2），结构上禁止无限横/纵走导致的异常压缩 |
| 路径窗口 | Sakoe-Chiba 带：`|i - j| ≤ window`（默认 10，`null` 表示不带） |
| 归一化 | `normalized_cost = total_cost / len(path)`，分母固定为匹配对数（路径长度），跨对齐比较只用归一值 |
| 不可达终点 | 显式失败：库层抛 `UnreachablePathError`，API 返回 `status="unreachable"` 及原因 |
| 局部伸缩率 | 路径局部斜率 dj/di（±5 步居中窗口）；纯竖直段为无界（API 中为 `null`） |

终点不可达有两类明确判定：端点落在带外（预检查）与斜率约束下无合法步序列（DP 累计为 ∞）。

## 模块关系

```
config/default.yaml          配置（度量/步型/窗口/归一化/存储策略/流参数）
src/dtw_service/
  settings.py     加载并校验配置（DTW_CONFIG 环境变量可覆盖路径）
  contracts.py    样本契约：AlignRequest/AlignResponse、状态词汇（ok/unreachable）
  constraints.py  步型、斜率约束、Sakoe-Chiba 窗口、路径合法性校验
  core.py         带状 DP 累计器 + 回溯；O(n·(2w+1)·(1+2R)) 存储，大矩阵可用
  stretch.py      局部/全局伸缩率估计
  stream.py       流状态：滑动缓冲 + 快照决策（ok/unreachable/undetermined）
  diagnostics.py  请求标识、脱敏（形状+哈希，不打印原始值）、决策日志
  api.py          FastAPI 表面：POST /v1/dtw/align、GET /healthz
tests/
  reference.py               独立穷举参考（纯 Python 递归枚举全部合法路径）
  test_exhaustive_reference.py  短序列上 DP 核心 vs 穷举参考逐一对拍
  test_core.py / test_constraints.py / test_stretch.py / test_stream.py / test_api.py
```

`core.py` 只依赖 `constraints.py`；`api.py`/`stream.py` 组合核心与诊断；测试不依赖被测核心生成参考答案（`tests/reference.py` 为独立实现）。

## 本地验证命令

```bash
cd b
python3 -m venv .venv && source .venv/bin/activate   # 可选
pip install -r requirements.txt

# 1) 全部测试（138 个）：穷举对拍、速度变化、局部缺段、空序列、过窄窗口、
#    路径单调性、归一化约定、带状/稠密一致性、流状态、API 契约
python3 -m pytest

# 2) 启动服务并冒烟
PYTHONPATH=src python3 -m uvicorn dtw_service.api:app --port 8471
curl -s http://127.0.0.1:8471/healthz
curl -s -X POST http://127.0.0.1:8471/v1/dtw/align -H 'content-type: application/json' \
  -d '{"query": [[0.0],[1.0],[2.0]], "reference": [[0.0],[1.0],[1.5],[2.0]], "window": 3}'
```

预期判断方式：

- `pytest` 全绿（当前 138 passed）。穷举对拍用例（`test_exhaustive_reference.py`，108 个参数组合）要求 DP 总代价与独立枚举的最优代价在 1e-9 内一致，且路径通过合法性校验。
- `/v1/dtw/align` 正常时 `status="ok"`，且 `normalized_cost == total_cost / path_length`（客户端可复算）；`path` 首末为 `(0,0)`、`(n-1,m-1)` 且单调。
- 终点不可达时 HTTP 200 + `status="unreachable"` + 明确 `reason`（如 `endpoint lies outside the Sakoe-Chiba band`），不是嚎 500。
- 空序列、维度不一致、非有限值 → HTTP 422（rejected 类别），错误响应不回显原始输入值。
- 响应头 `X-Request-ID` 与响应体 `request_id` 一致；日志中的决策记录只含形状与哈希（脱敏）。

## 依赖版本（已锁定，requirements.txt）

Python 3.12.3；numpy 2.4.6、scipy 1.15.3、fastapi 0.141.1、pydantic 2.13.5、
uvicorn 0.54.0、pyyaml 6.0.1；测试：pytest 9.1.1、httpx 0.28.1。

## 测试状态

- 已运行：`python3 -m pytest` → **138 passed**（2026-10-03，本机 Python 3.12.3）。
- 唯一的警告：starlette TestClient 关于 httpx 的弃用提示，不影响结果。
- 未覆盖：uvicorn 多 worker 部署、并发压力测试（未运行，非本契约范围）。
