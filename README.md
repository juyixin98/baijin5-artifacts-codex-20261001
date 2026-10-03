# SOS IIR 音频过滤服务

二阶节（second-order sections, SOS）级联 IIR 音频过滤服务：FastAPI + NumPy/SciPy，
支持**分块流式处理**、**多声道状态隔离**与**参数版本管理**。

## 结构

```
app/
  config.py            # 资源上限与数值策略（SOSFILT_* 环境变量可覆盖）
  errors.py            # 错误分类学：input/not_found/state_conflict/resource/computation
  schemas.py           # Pydantic 请求/响应契约
  main.py              # FastAPI 入口，run-id 中间件，异常 -> 分类 JSON
  dsp/
    coefficients.py    # a0 规范化 + 极点稳定性检查
    cascade.py         # 转置 DF-II 级联核心，逐 (节, 声道) 状态
  state/
    store.py           # 流注册表：参数版本、乐观并发、资源限额
tests/
  reference.py         # 独立参考实现（纯 Python 直接 I 型差分方程）
  test_coefficients.py # 规范化/稳定性契约
  test_cascade.py      # 脉冲/阶跃/多声道/近单位圆极点/溢出
  test_chunking.py     # 整段 vs 任意切块逐位一致
  test_api.py          # HTTP 契约与错误类别
  logs/                # 每次 pytest 会话的 JSONL 运行日志（可重放）
examples/
  client_demo.py       # 端到端演示（建流 -> 分块 -> 切参 -> 409 -> 删除）
requirements.txt       # 锁定依赖
```

## 快速开始

```bash
pip install -r requirements.txt
python3 -m pytest                 # 31 个独立测试
uvicorn app.main:app --port 8000  # 启动服务
python3 examples/client_demo.py   # 另开终端运行演示
```

## 算法与契约

### 滤波核心

每节转置直接 II 型（transposed DF-II）：

```
y[n] = b0*x[n] + z1
z1'  = b1*x[n] - a1*y[n] + z2
z2'  = b2*x[n] - a2*y[n]
```

状态形状为 `(n_sections, n_channels, 2)`，**每声道每节完全隔离**。
块语义是原子的：整块算完且全部有限才提交状态；溢出块不污染流状态。

### 系数契约

- 每节 `[b0,b1,b2,a0,a1,a2]`，入库前除以 `a0` 规范化；
- `a0 == 0`、任何非有限系数 → `input_error` (422)；
- 极点半径 `>= 1.0`（不稳定或临界稳定）→ `input_error` (422)，
  响应中带回违规节索引与极点半径；
- 节数超过 `SOSFILT_MAX_SECTIONS`（默认 32）→ `resource_exhausted` (429)。

### 初始条件与参数切换瞬态

- `initial_condition: "zero"`：滤波器从静止开始（默认）；
- `initial_condition: "dc_steady"`：状态预充到单位阶跃稳态，消除阶跃瞬态；
- 切换系数时 `transient: "preserve_state"` 保留状态（会产生可预期的切换瞬态，
  适合平滑过渡场景），`"reset_state"` 清零状态（输出不连续但行为等同新滤波器）。
  节数变化时旧状态无意义，强制清零。

### 参数版本

每个流持有 `param_version`（创建时为 1，每次成功换参 +1）：

- 处理块可携带 `param_version` 钉住版本，不匹配 → 409 `state_conflict`；
- 换参必须携带 `expected_version`（乐观并发令牌），不匹配 → 409。

### 非有限输出不静默清零

输出出现 NaN/Inf 时抛出 `computation_failed` (500)，该块被拒绝且流状态保持
不变；输入含 NaN/Inf 则是 `input_error` (422)。

### 错误类别

| category              | HTTP | 含义                         |
|-----------------------|------|------------------------------|
| `input_error`         | 422  | 系数/样本块非法              |
| `not_found`           | 404  | 流不存在                     |
| `state_conflict`      | 409  | 参数版本冲突                 |
| `resource_exhausted`  | 429  | 节数/声道/块长/流数超限      |
| `computation_failed`  | 500  | 数值计算失败（如溢出）       |

每个错误响应与每个正常响应（`X-Run-Id` 头）都带 `run_id`，可用于问题重放。

## API 示例

```bash
# 建流（一节低通）
curl -s -X POST localhost:8000/streams -H 'Content-Type: application/json' -d '{
  "sample_rate": 48000, "num_channels": 2,
  "coefficients": [[0.2929, 0.5858, 0.2929, 1.0, -0.0, 0.1716]]
}'
# -> {"stream_id": "...", "param_version": 1, ...}

# 分块处理（samples[i][c] = 第 i 个样本第 c 声道），钉住版本 1
curl -s -X POST localhost:8000/streams/<id>/blocks -H 'Content-Type: application/json' -d '{
  "samples": [[1.0, 0.0], [0.5, 0.0], [0.25, 0.0]],
  "param_version": 1
}'

# 换参（乐观并发 + 状态清零）
curl -s -X PUT localhost:8000/streams/<id>/coefficients -H 'Content-Type: application/json' -d '{
  "coefficients": [[1.0, 0.0, 0.0, 1.0, -0.5, 0.2]],
  "expected_version": 1, "transient": "reset_state"
}'
```

## 验证方案

31 个测试全部真实执行并通过（`31 passed`）：

- **脉冲响应** vs `scipy.signal.sosfilt` 与纯 Python 直接 I 型参考，误差 < 1e-12；
- **阶跃响应**稳态值等于解析直流增益 `prod(sum(b)/sum(a))`；
- **多声道**：静默声道逐位为零，受驱声道与单声道运行逐位一致；
- **近单位圆极点**（r=0.9999 谐振器，5000 样本）与参考实现误差 < 1e-9 且输出有限；
- **整段 vs 任意切块**（含逐样本 364 个块）输出**逐位一致**——因为每样本运算
  顺序与切块无关，这是强断言而非容差断言；
- **溢出**：1e308 输入经增益节产生 inf → `computation_failed`，状态未提交；
- **API**：版本冲突 409、坏系数 422、未知流 404、超限 429、换参后行为等同新滤波器。

测试日志在 `tests/logs/run_<run_id>.jsonl`：每行含 run_id、测试名、结果、
种子、容差、关键中间量（误差范数、极点半径、状态是否提交等）与判断理由，
可据此重放失败。

## 剩余限制

- 滤波核心为逐样本 Python 循环，吞吐约每秒数百万样本量级；生产场景应换
  C/Numba 内核（状态模型与契约不变）。
- 流状态仅在内存中，重启即丢失；无持久化与鉴权（本地合成环境定位）。
- 样本经 JSON 传输，float64 十进制往返存在末位舍入（测试按 1e-12 容差断言，
  服务内部计算不损失精度）；高吞吐场景应换二进制帧（如 raw float32）。
- 稳定性判据为严格极点模 < 1；定点量化系数敏感度（系数量化后的极点漂移）
  未建模。
