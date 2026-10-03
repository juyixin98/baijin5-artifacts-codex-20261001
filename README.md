# MFCC + Δ/Δ² 后端（无外部模型依赖）

合成语音样本的 MFCC 及一阶/二阶差分特征后端。纯 NumPy/SciPy 信号处理，
FastAPI 提供服务入口；所有输入为本地合成夹具（正弦、白噪声、静音、极短
输入），不需要任何生产账号或真实业务数据。

## 工程结构

```
mfcc_backend/            # 库 + 服务
  config.py              # MFCCConfig：全部算法常量的唯一来源（冻结规范）
  errors.py              # 类型化错误层级，每类错误有稳定 category
  framing.py             # 预加重、分帧、Hamming 窗、样本契约校验
  filterbank.py          # HTK Mel 滤波器组、频轴、空支撑检测
  pipeline.py            # 批处理流水线（返回全部中间矩阵）
  delta.py               # Δ/Δ²，边界延拓规则：边缘复制
  streaming.py           # 流式提取器（上下文管理 + 无未来泄漏）
  contracts.py           # pydantic 请求/响应契约、版本信息
  api.py                 # FastAPI 入口
tests/
  reference_impl.py      # 独立参考实现（字面循环，与被测代码零共享）
  conftest.py            # run_id、结构化测试日志、合成信号夹具
  test_*.py              # 配置/分帧/滤波器/流水线/差分/流式/API
scripts/demo.py          # 本地演示：批处理 vs 流式 vs HTTP API
requirements.txt
```

## 固定算法规范

| 阶段 | 规范 |
|---|---|
| 预加重 | `y[n] = x[n] − 0.97·x[n−1]`，`y[0] = x[0]`（流式跨块携带 1 个原始样本） |
| 分帧 | 25 ms 窗 / 10 ms 移（16 kHz 下 400/160 样本），只发完整帧，尾部不足一帧丢弃 |
| 窗函数 | 对称 Hamming：`w[k] = 0.54 − 0.46·cos(2πk/(N−1))` |
| 功率谱 | `rfft`（n_fft=512），`P = |X|² / n_fft` |
| Mel 刻度 | HTK：`mel(f) = 2595·log10(1 + f/700)` |
| 滤波器组 | 26 个三角滤波器，fmin=20 Hz，fmax=Nyquist，不做面积归一化 |
| log 下限 | `ln(max(E, 1e-10))`，任何输入都不会产生 `-inf` |
| DCT | DCT-II，`norm="ortho"`，取前 13 维 |
| 差分 | 宽度 2 的回归窗，**边界延拓 = 边缘复制**（首/末帧各复制 2 次） |

帧数公式：`n_frames = 1 + (n_samples − 400) // 160`（`n_samples ≥ 400`，否则 0）。
1 秒 @16 kHz → 98 帧。

## 采样率与频轴、空支撑检测

FFT 频轴完全由采样率决定：bin k 位于 `k · sample_rate / n_fft` Hz。
当 Mel 间距细于 FFT bin 间距（低 n_fft、多滤波器、低采样率）时，某些
滤波器在频轴上没有任何非零权重——**空支撑**。本实现将其检测为
`EmptyFilterError`（category=`filterbank_empty_support`）并列出滤波器
索引，而不是悄悄落入 log 下限伪装成正常结果。

## 流式语义

- **无未来泄漏**：帧 t 仅在它依赖的全部样本到达后才发出。Δ 需要未来
  `delta_width` 帧、Δ² 需要 `2·delta_width` 帧，因此发出滞后
  `2·delta_width` 帧（默认 4 帧）。
- **留足上下文**：跨块携带 1 个预加重原始样本；分帧余量从"下一帧起点"
  保留（重叠样本不丢）；已发出的最近 `2·delta_width` 行 MFCC 作为差分
  左上下文，边界处使用真实历史帧而非边缘复制（流起点除外，与批处理一致）。
- **批流一致**：同参数同输入下，任意分块方式的流式输出与批处理一致
  （浮点归约顺序差异 ~1e-13，测试容差 1e-9，远小于任何边界 bug 的 O(1) 信号）。
- `finalize()` 用边缘复制规则冲刷滞留帧并关闭流；关闭后再推块或重复
  finalize 抛 `StreamStateError`。

## 错误语义

| 情况 | 结果 |
|---|---|
| 正常输入 | 200，`n_frames ≥ 1` |
| 输入不足一帧（如 120 样本） | 200，`n_frames=0`，`warnings` 非空（**不是错误**） |
| 空样本数组 | 422（schema `min_length=1`） |
| NaN/inf、超长（>300 s） | 400，`category=input_contract` |
| 非法配置（n_fft<帧长、fmax>Nyquist 等） | 400，`category=config`，`details.problems` 列出全部违例 |
| 滤波器空支撑 | 400，`category=filterbank_empty_support`，`details.empty_filters` 列出索引 |
| finalize 后推块 / 重复 finalize | 400，`category=stream_state` |
| 未知流会话 | 404 |
| 未知异常 | 500（**绝不**伪装成成功响应） |

每个错误响应都带 `run_id`，可与测试日志/客户端日志关联。

## 复现步骤

```bash
pip install -r requirements.txt

# 运行测试（实际执行并报告结果；结构化日志写入 test_logs/session-<run_id>.jsonl）
python -m pytest

# 本地演示：批处理 vs 流式 vs HTTP API + 错误语义，全部检查通过才退出 0
python scripts/demo.py

# 启动服务
python -m uvicorn mfcc_backend.api:app --port 8000
curl http://127.0.0.1:8000/healthz
```

### API 摘要

- `GET  /healthz` — 状态与版本（service/python/numpy/scipy）
- `GET  /v1/config` — 当前默认配置
- `POST /v1/mfcc` — 批处理：`{samples, sample_rate, config?}` →
  `{run_id, n_frames, mfcc, delta, delta_delta, warnings, config, versions}`
- `POST /v1/stream` — 创建流会话 → `session_id`
- `POST /v1/stream/{id}/chunks` — 推一个块，返回本次发出的帧
- `POST /v1/stream/{id}/finalize` — 冲刷并关闭会话

请求级 `config` 可覆盖任意 `MFCCConfig` 字段；合并后的配置先按请求
`sample_rate` 校验再使用，并随响应回显，保证结果可复现。

## 测试设计要点

- **参考答案独立性**：`tests/reference_impl.py` 用字面循环直写公式
  （DFT 求和、逐 bin 三角求值、显式 DCT-II、显式差分），与被测的向量化
  实现零共享代码；解析断言（静音 → `c0 = √26·ln(1e-10)`、斜坡 → 斜率、
  常数 → 0）来自封闭形式，不经过任何实现。
- **边界输入**：0/399/400/560 样本的帧数边界、120 样本极短输入、静音、
  微小能量（log 下限）、空支撑配置、NaN、非法配置逐项断言失败类别。
- **流式**：5 组随机分块 + 单样本病态分块与批处理对比；逐块前缀性质
  验证无未来泄漏；发出滞后恰为 `2·delta_width`。
- **日志**：每条测试步骤记录 run_id、测试名、输入夹具身份、判定依据
  （basis）与关键数值，写入 `test_logs/session-<run_id>.jsonl` 并回显
  控制台；会话开始打印 python/numpy/scipy/pytest 版本。
