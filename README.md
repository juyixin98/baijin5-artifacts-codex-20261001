# LPC 分析/合成后端

音频帧线性预测（LPC）后端：自相关法估计预测系数、计算残差信号、并经由对应的
合成滤波器重构信号。技术栈：Python 3.12、FastAPI、NumPy、SciPy。所有数据均为
本地合成夹具（固定随机种子），无需任何外部账号或真实业务数据。

## 算法契约（固定，非演示硬编码）

- **方法**：自相关法（autocorrelation method）。帧先加窗再求有偏自相关
  `r[k] = Σ x[n]·x[n-k]`，正则方程矩阵为 Toeplitz。
- **窗**：默认 Hann（`hann`/`hamming`/`rect` 可选，默认与上限见
  `app/config.py`，可用 `LPC_*` 环境变量覆盖）。
- **阶数**：默认 10，必须小于帧长。
- **求解**：Levinson-Durbin 递归（`app/lpc/levinson.py`），每步监控：
  - `|反射系数| ≥ 1 - 1e-9` → 错误诊断 `REFLECTION_COEFFICIENT_UNSTABLE`；
  - 预测误差能量 E 塌陷（≤1e-14，即自相关矩阵数值奇异，典型原因是阶数过高）
    → 错误诊断 `PREDICTION_ERROR_ENERGY_COLLAPSED`，递归停止；
  - `|k|` 接近 1 → 警告 `REFLECTION_COEFFICIENT_MARGINAL`（不确定结论，单列）。
- **零能量帧**（有定义的行为）：`r[0] ≤ 1e-12` 时返回系数 `[1,0,…,0]`、
  增益 0、零残差，附 `ZERO_ENERGY_FRAME`（info 级）诊断。
- **滤波器初始状态对应**：分析滤波器（FIR，`e = A(z)x`）的状态是最近 p 个
  输入样本；合成滤波器（IIR，`x̂ = e/A(z)`）的状态是最近 p 个输出样本。合成
  滤波器要精确逆转分析，其初始状态必须等于分析滤波器在同一时刻的历史
  （`app/lpc/filters.py: corresponding_synthesis_state`）。两者均从零状态
  起步时，逐帧流式处理自动保持该对应关系。
- **重构核验**：只接受「重构信号 vs 原始信号」的相对误差（≤1e-9）作为无损
  证据；**残差能量小本身不构成无损证据**（预测器构造上残差就小，即便合成
  初始状态错误）。该原则写在 `app/lpc/pipeline.py: VERIFICATION_NOTE`，并有
  专门测试（`test_small_residual_alone_is_not_lossless_evidence`）。

## 目录结构

```
app/
  config.py          # 固定默认值 + LPC_* 环境变量覆盖
  contracts.py       # 请求/响应契约（Pydantic），错误包络
  logging_config.py  # 带 request_id 的结构化日志
  lpc/
    autocorr.py      # 有偏自相关
    windowing.py     # 固定窗函数
    levinson.py      # Levinson-Durbin + 稳定性诊断
    toeplitz_ref.py  # 独立参考：scipy Toeplitz 直接求解
    filters.py       # 有状态分析/合成滤波器（状态对应）
    pipeline.py      # 帧流水线 + 往返核验
  stream.py          # 流会话（跨帧滤波器状态）
  service.py         # 业务编排 + 关键步骤日志
  main.py            # FastAPI 入口、请求身份中间件、错误包络
fixtures/
  generate_fixtures.py  # 确定性夹具生成器（种子 20260927）
  data/                 # 已生成并提交的样例数据（JSON）
tests/
  unit/            # 算法单元测试（手算值 / SciPy 参考 / 失败类别）
  integration/     # API 端到端测试（TestClient）
```

## 快速开始

```bash
pip install -r requirements.txt

# 重新生成样例数据（可选，已提交在 fixtures/data/）
python -m fixtures.generate_fixtures

# 运行全部测试
python -m pytest

# 启动服务
python -m uvicorn app.main:app --port 8000
```

调用示例：

```bash
curl -s http://127.0.0.1:8000/health
# {"status":"ok","version":"0.1.0"}

curl -s -X POST http://127.0.0.1:8000/v1/lpc/roundtrip \
  -H 'Content-Type: application/json' -H 'X-Request-ID: demo-1' \
  -d '{"samples": [...], "order": 10}'
```

响应的 `meta` 块携带 `request_id`（与 `X-Request-ID` 头及服务日志一致）、
`version`、`pipeline_version` 和执行过的流水线阶段；`analysis.errors` 与
`analysis.warnings` 分别单列失败原因与不确定结论。

## API 一览

| 方法/路径 | 说明 |
|---|---|
| `GET /health` · `GET /v1/version` | 健康检查 / 版本与默认配置 |
| `POST /v1/lpc/analyze` | 单帧分析：系数、反射系数、增益、残差、诊断 |
| `POST /v1/lpc/synthesize` | 由系数+残差（+可选初始状态）重构 |
| `POST /v1/lpc/roundtrip` | 分析+合成，返回相对重构误差与核验结论 |
| `POST /v1/lpc/streams` | 创建流会话（固定阶数/窗，携带滤波器状态） |
| `POST /v1/lpc/streams/{id}/frames` | 推流帧（`analyze` 或 `roundtrip`） |
| `GET`/`DELETE /v1/lpc/streams/{id}` | 查询/删除流会话状态 |

`analyze`/`roundtrip` 支持 `verify_toeplitz: true`：用独立的
`scipy.linalg.solve_toeplitz` 参考解交叉核验 Levinson-Durbin 结果，
偏差超过 1e-6 时给出 `TOEPLITZ_CROSSCHECK_MISMATCH` 警告（阶数过高夹具
即触发此类）。

## 验证设计（测试即文档）

夹具（`fixtures/data/`，全部本地合成、种子固定）：

- `ar_process.json` — 已知真实系数的 AR(4) 过程（极点 0.9/0.85 两对共轭），
  由 `scipy.signal.lfilter` 生成（不经过被测核心）；
- `sine.json` — 440 Hz 单频正弦；
- `silence.json` — 零能量帧；
- `rank_deficient.json` — 双正弦叠加（有效秩约 4），配合阶数 20 构成
  「阶数过高」夹具。

参考答案均不来自被测核心自身：手算递推值（`r=[4,2,1]`）、FFT 卷积定理、
`scipy.linalg.solve_toeplitz`、`scipy.signal.lfilter`、夹具的真实 AR 系数
与正弦理论系数 `[-2cos(ω), 1]`。

失败类别断言（非「接口能调通」）：

- 非法自相关 `r=[1,1]` → `REFLECTION_COEFFICIENT_UNSTABLE`（error）；
- 理论正弦自相关 `r[k]=cos(ωk)` 阶数 7 → `PREDICTION_ERROR_ENERGY_COLLAPSED`，
  递归止于阶 2；
- 阶数过高夹具 + Toeplitz 交叉核验 → `TOEPLITZ_CROSSCHECK_MISMATCH`（warning）；
- 零能量帧 → `ZERO_ENERGY_FRAME`（info），系数/增益/残差为定义值；
- 错误的合成初始状态 → 重构相对误差 > 1e-3 且 `verified=false`，
  尽管残差能量比 < 0.2（残差小 ≠ 无损）；
- `order ≥ 帧长` → 422 `INVALID_FRAME_PARAMETERS`；NaN 样本 → 422
  `VALIDATION_ERROR`；未知流会话 → 404 `STREAM_NOT_FOUND`。

## 真实测试命令与输出结论

```text
$ python -m pytest
39 passed, 1 warning in 0.76s
```

（1 条 warning 为 starlette TestClient 的弃用提示，与功能无关。）

服务冒烟（`python -m uvicorn app.main:app --port 18097`，对 440 Hz 正弦
160 样本、阶数 2 做 roundtrip）实测输出：

```text
coefficients: [1.0, -1.9687, 0.999]        # 理论值 [-2cos(2π·440/16000), 1] ≈ [-1.9702, 1]
max_pole_magnitude: 0.9995                 # 单频信号极点贴近单位圆，符合预期
metrics: {'relative_error': 3.07e-15, 'verified': True, 'residual_energy_ratio': 3.6e-04}
```

日志与请求身份关联（同一 `request_id=demo-1` 贯穿响应头、响应体与日志行）：

```text
INFO lpc.api request_id=demo-1 request start POST /v1/lpc/roundtrip
INFO lpc.api request_id=demo-1 request end POST /v1/lpc/roundtrip status=200 duration_ms=3.0
```
