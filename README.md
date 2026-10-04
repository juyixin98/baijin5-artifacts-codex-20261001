# stft-backend

短时傅里叶变换（STFT）及逆变换（ISTFT）后端。Python + FastAPI + NumPy + SciPy，
支持任意窗长 / 步长 / FFT 长度组合，所有数据均为本地合成，无外部账号或生产依赖。

## 核心约定（三个"统一"）

1. **统一窗函数**：同一窗缓冲用于分析、合成与 OLA 归一化分母（w² 的重叠相加）。
   窗为对称窗（`fftbins=False`），当 `win_length < n_fft` 时居中放入 `n_fft` 缓冲。
2. **统一中心填充**：输入两侧各补 `n_fft // 2` 个零，因此第 `k` 帧以输入样本
   `k * hop_length` 为中心（`frame_center_sample(k, hop) = k * hop`）。
   帧数保证覆盖整个补零信号——保留区两侧各留 `pad` 余量，否则对称窗端点的零
   会让保留区边缘的 OLA 分母为零。
3. **统一最终裁剪**：逆变换在重叠相加、归一化之后去掉填充并裁剪到请求的确切
   长度，**原始长度精确保留**。

**可重构性检查**：OLA 归一化分母在保留区上必须非零（相对容差
`1e-8 * max(w²)`）。`hop_length > win_length` 在参数校验时静态拒绝；
更隐蔽的情形（如 `hop == n_fft` 配 hann 窗，帧边界样本恰好落在窗的零端点上）
在 ISTFT 时由分母检查动态拒绝，均报 `NOT_RECONSTRUCTIBLE`。

**单边谱展开**：`/v1/expand` 恢复共轭对称，直流 bin 不重复；`n_fft` 为偶数时
Nyquist bin 不重复，奇数时无 Nyquist bin。

## 模块划分

| 模块 | 职责 |
|---|---|
| `app/stft_core.py` | 信号算法：参数校验、统一窗、STFT/ISTFT、OLA 分母检查、单边→全谱展开、帧索引↔样本位置映射 |
| `app/contracts.py` | 样本契约：请求/响应 Pydantic 模型、错误信封 |
| `app/stream.py` | 流状态：`StreamStft`（分块分析，绝对样本簿记）、`StreamOla`（增量 OLA 合成，只发射已最终确定的保留区样本） |
| `app/errors.py` | 类型化失败类别（机器可读 `code`） |
| `app/main.py` | FastAPI 表面：请求 ID 中间件、端点、异常映射 |
| `app/config.py` | 运行配置（环境变量覆盖，无密钥） |
| `app/logging_setup.py` | 日志：每条记录携带 request_id |
| `tests/` | 数值测试、流状态测试、API 契约测试 |

## 本地启动

```bash
pip install -r requirements.txt   # 锁定版本，见下
python -m uvicorn app.main:app --port 8000
```

配置（环境变量）：`STFT_MAX_SAMPLES`（默认 2e6）、`STFT_MAX_FRAMES`（默认 2e5）、
`STFT_LOG_LEVEL`（默认 INFO）。

## 示例请求

正向变换（响应含 `request_id`、`frame_centers` 等可解释字段）：

```bash
curl -s -X POST http://127.0.0.1:8000/v1/stft \
  -H 'Content-Type: application/json' \
  -d '{"samples": [0.0, 1.0, 0.0, -1.0, 0.5, -0.5, 0.25, 0.125],
       "params": {"n_fft": 8, "win_length": 8, "hop_length": 4, "window": "hann"}}'
```

```json
{"request_id": "84f22c471a94", "version": "0.1.0", "n_samples": 8,
 "n_frames": 3, "n_bins": 5, "hop_length": 4, "frame_centers": [0, 4, 8],
 "spectrogram": [{"real": [...], "imag": [...]}, ...]}
```

逆变换（把上一步的 `spectrogram` 原样送回，`n_samples` 给原始长度）：

```bash
curl -s -X POST http://127.0.0.1:8000/v1/istft \
  -H 'Content-Type: application/json' \
  -d '{"spectrogram": [...], "params": {...}, "n_samples": 8}'
# -> {"n_samples": 8, "min_ola_denominator": 0.409...,
#     "samples": [0.0, 1.0, 0.0, -1.0, 0.5, -0.5, 0.25, 0.125...]}
```

失败示例（`hop > win_length`），返回类型化错误信封，HTTP 400：

```json
{"request_id": "2d432cbf9bb7",
 "error": {"code": "NOT_RECONSTRUCTIBLE",
           "message": "hop_length (8) > win_length (4) leaves uncovered gaps; ..."}}
```

## 失败类别（`error.code`）

| code | HTTP | 含义 |
|---|---|---|
| `INVALID_CONFIG` | 400 | 单参数越界、未知窗名、`win_length > n_fft` |
| `NOT_RECONSTRUCTIBLE` | 400 | OLA 分母在保留区为零（静态或动态检出） |
| `SHAPE_MISMATCH` | 400 | 谱形状与参数不符、请求长度超出可重构范围 |
| `EMPTY_SIGNAL` | 400 | 空信号 |
| `SCHEMA_VALIDATION` | 422 | 请求体不符合契约（Pydantic） |
| `PAYLOAD_TOO_LARGE` | 413 | 超出配置的大小上限 |

## 可解释性

每个请求分配 `request_id`（响应体与 `X-Request-ID` 头均携带），所有日志行
都带该 ID，并记录关键步骤（参数、帧数、最小 OLA 分母）与失败原因：

```
2026-10-04 10:25:53 INFO [2d432cbf9bb7] stft_backend: POST /v1/stft started
2026-10-04 10:25:53 WARNING [2d432cbf9bb7] stft_backend: request failed: code=NOT_RECONSTRUCTIBLE reason=hop_length (8) > ...
```

## 流式处理

`StreamStft` 不做中心填充：第 `k` 帧起于绝对样本 `k * hop`，收满 `n_fft`
个样本即发射；`flush()` 补零发射尾部帧，保证每个收到的样本都被覆盖。
`StreamOla` 反向累积，只在样本"最终确定"（所有可能覆盖它的帧都已到达）
后才发射，且只对保留区（默认 `kept_offset = pad`，与批处理约定一致）做
分母校验与归一化。push-after-flush 等非法状态转换报 `STREAM_STATE`（409）。

## 支持范围与关键取舍

- 仅实数一维信号、单边谱（rfft/irfft）；复数谱以 `{real, imag}` 分量传输。
- 窗：`hann`（默认）、`hamming`、`blackman`、`rect`；对称窗而非周期窗——
  周期窗在 OLA 归一化下边界行为更差，对称窗配合中心填充 + 裁剪可精确往返。
- 中心填充用**零填充**而非反射填充：确定性、无信号假设，边界脉冲可精确重构
  （有测试覆盖）。
- 同步、进程内、无持久化：面向本地合成工作负载；大小上限由配置把关。
- 浮点为 float64/complex128，往返误差典型值 < 1e-9。

## 测试

```bash
python -m pytest          # 42 个用例
```

覆盖：奇/偶窗长与 n_fft 组合、边界脉冲、短于一窗的信号、帧索引→样本位置
映射、谱形状不匹配、不可重构参数（静态与动态路径）、流式与批处理等价性、
API 契约与失败类别。参考答案由测试内的朴素 O(n²) DFT 与手算期望值给出，
不依赖被测核心。当前状态：42 passed，无跳过、无预期失败。
