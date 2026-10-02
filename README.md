# watershed-backend

确定性标记分水岭分割后端。给定梯度图与种子标记，执行 Meyer 浸没式分水岭
泛洪，输出盆地标签图与边界脊线。所有输入均为本地合成夹具，无外部账号
或真实业务数据依赖。

## 算法假设（契约固定，非队列偶然）

- **浸没泛洪，非最近种子距离**：水从种子出发按水位逐级上升，像素只有在
  某盆地的洪锋在当前水位到达它时才被认领；输出同时包含盆地（标签 > 0）
  与脊线（标签 0）。
- **固定连通邻域**：8 连通（默认）或 4 连通，按请求指定，运行中不变；
  邻居偏移按固定行优先顺序枚举。
- **平台顺序确定**：优先队列键为 `(高程, 入队序号)`，序列为全局单调计数；
  种子按行优先扫描入队，邻居按固定偏移顺序入队，每个像素至多入队一次。
  相同高程像素的处理顺序完全由输入决定。注意浸没语义：新入队的低高程
  像素会排在已入队的高高程像素之前（水位先淹没低处）。
- **固定脊线定义**：已标记邻居含 ≥ 2 个不同标签的像素成为脊线（标签 0），
  脊线像素是终态，不再传播。
- **冲突种子与无种子区**：同一像素被两个不同标签声明 → `SEED_CONFLICT`，
  拒绝而非静默裁决；掩膜连通分区内无任何种子的活跃像素标记为 -1
  （UNREACHED）并计入统计与警告，不被静默吞并。

## 模块关系

```
config/default.yaml          配置层（YAML 默认 + WATERSHED_* 环境变量覆盖）
src/watershed_backend/
  config.py                  Settings 加载与校验
  errors.py                  类型化错误，每类失败有稳定 category
  contracts.py               图像数据契约：形状/有限性/种子冲突/掩膜校验
  kernel/watershed.py        数值内核：确定性浸没泛洪（heapq 优先队列）
  jobs/store.py              作业记录与内存存储（PENDING/RUNNING/COMPLETED/FAILED）
  jobs/runner.py             分块作业：validate→flood→extract_boundary→finalize
                             四阶段，flood 按像素块回报进度
  imageio.py                 Pillow PNG 编解码（16 位灰度；标签含 +1 偏移约定）
  logging_utils.py           JSON 结构化日志 + 输入 SHA-256 指纹
  api/app.py                 FastAPI 验证接口（同步执行，结果可回查）
tests/                       独立测试层（夹具 + 内核/契约/作业/API/编解码）
scripts/freeze_noise_fixture.py  重新生成噪声回归锚点（仅在内核有意变更后）
scripts/run_validation.sh    本地验证入口
```

数据流：HTTP 请求 → pydantic 模式 → `contracts` 校验 → `kernel` 泛洪 →
`jobs` 记录阶段/进度/统计 → JSON 响应。日志行携带 `job_id`、输入
SHA-256、阶段名与运行时版本，可关联到具体输入与计算步骤。

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/v1/health` | 存活检查 |
| GET  | `/v1/version` | 影响输出的运行时版本 |
| POST | `/v1/jobs` | 提交并执行分割（JSON 数组；`seeds` 或 `markers` 二选一） |
| POST | `/v1/segment/image` | 梯度以 base64 16 位 PNG 提交 |
| GET  | `/v1/jobs/{id}` | 状态、阶段耗时、进度、错误 |
| GET  | `/v1/jobs/{id}/result` | 结果；未完成返回 409 |

失败语义：契约违反 → 422 + `error.category`（如 `SEED_CONFLICT`、
`EMPTY_MARKERS`、`SHAPE_MISMATCH`、`NON_FINITE_GRADIENT`、`IMAGE_TOO_LARGE`、
`SEED_OUT_OF_BOUNDS`、`SEED_OUTSIDE_MASK`、`INVALID_CONNECTIVITY`、
`INVALID_LABEL`）；内核不变量违反 → 500 + `KERNEL_INVARIANT`；未知作业 →
404。异常或未知状态不会统一返回成功。

## 依赖版本（验证时实测）

- Python 3.12.3
- numpy 2.4.6, scipy 1.15.3, pillow 10.2.0
- fastapi 0.141.1, pydantic 2.13.5
- 测试：pytest 9.1.1, httpx（TestClient）

`requirements.txt` 按上述版本钉住核心依赖。

## 本地验证

```bash
# 一键验证（打印版本 → 运行全部测试 → 非零退出即失败）
bash scripts/run_validation.sh

# 或单独运行
python3 -m pytest -v

# 启动服务
PYTHONPATH=src python3 -m uvicorn watershed_backend.api.app:app --port 8000
curl localhost:8000/v1/health
```

预期判断方式：退出码 0 且全部用例 PASSED 为通过；任何 FAILED/ERROR
都会指明夹具与被违反的行为或失败类别（如 `SEED_CONFLICT`）。

## 测试覆盖的行为

- **双盆地夹具**：手工逐像素推演的 5×5 参考（含"低高程后入队先处理"
  的浸没次序），断言精确标签图、脊线位置、统计账目（每像素有归属）。
- **平坦平台夹具**：等值平台脊线固定在第 3 列直线；4/8 连通结果一致；
  种子列表排列不影响输出。
- **鞍点夹具**：无种子局部极小被先到的洪锋确定性地吸收；鞍点像素成脊。
- **噪声梯度夹具**（固定随机种子）：种子保留、每盆地单连通分量
  （scipy.ndimage 独立验证）、全覆盖、脊线分隔、种子排列稳定、
  冻结回归锚点。
- **参考答案独立性**：双盆地/平台/鞍点的期望数组为手工推演字面量，非
  被测内核生成；噪声锚点是回归快照（`scripts/freeze_noise_fixture.py`
  生成，文件内注明），仅作辅助。
- **失败类别**：每种契约违反对应独立测试断言其 `category`；失败作业
  状态为 FAILED 且 result 为空，不返回成功。

## 测试状态

最近一次本地运行（2026-10-02）：**57 passed, 0 failed**（pytest 9.1.1，
Python 3.12.3）。无跳过、无未运行用例。唯一警告：starlette 关于
TestClient/httpx 的弃用提示，不影响判定。
