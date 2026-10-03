# mfcc-backend

合成语音样本的 MFCC + 一阶/二阶差分（Δ、ΔΔ）特征后端。纯本地实现，不依赖任何外部模型、
账号或真实业务数据；所有输入来自本地合成夹具（正弦、白噪声、静音、极短序列）。

技术栈：Python 3.12 / FastAPI / NumPy / SciPy。

## 固定的算法规范（不随输入隐式变化）

| 环节 | 规范 |
|---|---|
| 预加重 | `y[n] = x[n] − 0.97·x[n−1]`，边界 `x[−1] = 0`（`y[0] = x[0]` 不衰减） |
| 分帧 | 帧长 25 ms、帧移 10 ms（16 kHz 下 400/160 样本，round 换算）；帧锚定在样本 0；**不足一帧的尾部直接丢弃，不补零**；短于一帧 → `INSUFFICIENT_SIGNAL` |
| 窗 | 对称 Hamming：`w[n] = 0.54 − 0.46·cos(2πn/(N−1))`（固定，不可配置） |
| 频谱 | `nfft = 帧长`（不补零）；功率谱 `|rfft|²/nfft`；**频轴 `f[k] = k·sample_rate/nfft` 由采样率决定** |
| Mel 滤波器组 | HTK 刻度 `2595·log10(1+f/700)`；26 个三角滤波器，fmin=20 Hz，fmax=Nyquist；无面积归一化；**任一滤波器在频轴上无支撑（全零行）→ `EMPTY_FILTER_SUPPORT`，报出具体滤波器序号与频率分辨率** |
| 对数 | 自然对数；Mel 能量先取 `max(E, 1e-10)` 下限再取对数（静音输出为确定的常数矩阵） |
| DCT | DCT-II，`norm="ortho"`，取前 13 维 |
| 差分 | `Δ[t] = Σ_{n=1..2} n·(c[t+n] − c[t−n]) / (2·Σn²)`；**边界为边缘复制**（`c[−1]=c[0]`，`c[T]=c[T−1]`）；ΔΔ 对 Δ 施加同一规则 |
| 流式 | 预加重跨块只携带上一个原始样本（因果）；帧完整即出 MFCC；Δ 行须等第 `t+2` 帧到达才发出（**不泄漏未来样本**）；`finish()` 用与批处理相同的边缘复制规则释放末尾 Δ/ΔΔ；不足一帧的残余样本丢弃；0 帧时 `finish()` 报 `INSUFFICIENT_SIGNAL` |

## 目录结构

```
mfcc_backend/
  config.py       # 不可变配置 + 前置校验（所有算法常量的唯一来源）
  errors.py       # 错误分类：稳定的 code + HTTP 状态
  contracts.py    # 请求/响应 schema（样本契约层）
  fixtures.py     # 本地合成夹具（正弦/噪声/静音/极短/单帧，确定性）
  dsp/            # 信号算法层：preemphasis / framing / spectrum / mel / cepstrum / delta
  pipeline.py     # 批处理：返回全部中间矩阵
  streaming.py    # 流式状态机（因果上下文，无未来泄漏）
  service.py      # FastAPI 入口
tests/
  reference_impl.py  # 独立参考实现（显式循环，不 import 被测包）
  conftest.py        # 运行身份、版本、JSONL 测试日志
  test_*.py          # 配置 / 各阶段 / 参考对照 / 差分 / 流式 / API
scripts/demo.py   # 本地演示：批处理 + 流式等价 + 错误语义，退出码反映成败
```

## 快速开始

```bash
pip install -r requirements.txt

# 运行测试（实际执行并输出每个用例结果）
python -m pytest

# 本地演示（不需要启动服务器，进程内调用真实 FastAPI 应用）
python scripts/demo.py

# 启动服务
uvicorn mfcc_backend.service:app --port 8000
```

示例请求：

```bash
curl -s localhost:8000/health
curl -s -X POST localhost:8000/v1/features \
  -H 'content-type: application/json' \
  -d '{"sample_rate": 16000, "samples": [0.0, 0.1, ...], "include_intermediates": true}'
```

流式接口：`POST /v1/stream/sessions` 建会话 → `POST /v1/stream/sessions/{id}/chunks`
逐块推送 → `POST /v1/stream/sessions/{id}/finish` 收尾。同参数下，流式拼接结果与
`POST /v1/features` 批处理结果一致（测试断言 atol=1e-12）。

## 错误语义

失败永远不会以 HTTP 200 或空结果返回。错误体结构：
`{"error": {"code": ..., "message": ..., "detail": {...}}}`

| code | HTTP | 含义 |
|---|---|---|
| `INVALID_CONFIG` | 422 | 配置自相矛盾（如 `n_mfcc > n_mels`、hop > 帧长、fmax 超 Nyquist、log_floor ≤ 0） |
| `EMPTY_FILTER_SUPPORT` | 422 | Mel 滤波器在当前采样率/nfft 频轴上无支撑；detail 给出滤波器序号与频率分辨率 |
| `INVALID_AUDIO` | 400 | 样本非一维、为空、含 NaN/Inf；空流式块 |
| `INSUFFICIENT_SIGNAL` | 422 | 样本不足一帧（批处理），或流式 `finish()` 时 0 个完整帧 |
| `SESSION_NOT_FOUND` | 404 | 未知流式会话 id |
| `SESSION_STATE_ERROR` | 409 | 会话已结束后继续推块 / 重复 finish |

请求格式错误（缺字段、多余字段、空 samples 列表）由 schema 层返回 422。
未预期的异常以 500 暴露，不会被包装成成功响应。

## 数值验证策略（防“正常输入对、边界输入悄悄错”）

- `tests/reference_impl.py` 是与被测包**零共享代码**的独立参考实现（显式 Python 循环 +
  定义式 DCT 矩阵）。`test_pipeline_reference.py` 在正弦（含非整 bin 频率）、白噪声、
  静音、单帧输入上逐中间矩阵对照（预加重/分帧/功率谱/Mel 能量/log-mel/MFCC/Δ/ΔΔ）。
- 手算断言不来自被测实现：预加重边界 `y[0]=x[0]`；帧数公式 `1+(n−400)//160`；
  静音 `c0 = √26·ln(1e−10)`、其余系数为 0；单位斜坡 Δ 边缘精确值 `[0.5, 0.8, 1, …, 0.8, 0.5]`；
  440 Hz 正弦能量必须落在覆盖 440 Hz 的 Mel 带内。
- 流式无未来泄漏：每推一块，已发出的行必须与最终批处理同序号行完全一致，
  且 Δ 发出量 ≤ 已出帧数 − 2。
- 同参数跨批一致：两次批处理结果逐位相等；流式拼接 vs 批处理 atol=1e-12。

## 测试日志

每次运行写入 `tests/logs/test_run_<run_id>.jsonl`，记录：运行 id、Python/NumPy/SciPy/
FastAPI/被测包版本、每个用例的结果与耗时，以及关键用例的计算步骤记录（输入身份 =
样本数 + sha1、参数回显、最大偏差、判定依据）。控制台同步打印版本与日志路径。
