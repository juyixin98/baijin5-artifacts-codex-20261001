# wsola-backend

仅音频的 WSOLA（Waveform Similarity Overlap-Add）时间伸缩后端。输入单声道
PCM，输出伸缩后的音频以及**每段匹配偏移**（每帧选中的分析位置与自然位置之差）。
不改变音高；不承诺任意音频完全无伪影。

## 布局

```
src/wsola_backend/
  contracts.py     样本契约：校验、失败类别、判定词汇（框架无关）
  wsola.py         信号算法：WSOLA 核心、局部搜索、确定性平局规则
  stream.py        流状态：分块输入/增量输出，与批处理结果逐位一致
  metrics.py       数值指标：主频、接缝连续性、正弦残差 SNR、脉冲间隔
  service.py       请求流水线：校验 -> 伸缩 -> 诊断判定
  diagnostics.py   请求标识与判定日志（标签只记哈希，样本数据不入日志）
  api.py           FastAPI 表面（/v1/stretch, /healthz）
fixtures/synth.py  可复用合成夹具（纯音/脉冲列/静音/种子噪声），测试与验证共用
tests/             独立数值测试（pytest）
scripts/verify.py  端到端验证脚本，输出报告并以退出码标示成败
requirements.txt   固定版本依赖
```

## 安装与运行

```bash
pip install -r requirements.txt
python -m pytest                 # 74 项数值/契约/流/API 测试
python scripts/verify.py         # 场景验证报告（退出码 0 = 通过）
uvicorn --app-dir src wsola_backend.api:app --port 8000
```

API：`POST /v1/stretch`，体为
`{sample_rate, time_scale, samples_b64, input_label?}`，其中 `samples_b64`
是 little-endian float32 单声道 PCM 的 base64。响应含 `offsets`（每段匹配
偏移）、`match_positions`、`target_length`、`decision`、`notes` 与伸缩后音频。
契约违例返回 HTTP 422 与机器可读的 `failure_category`
（`empty_input / malformed_payload / non_finite_samples /
invalid_sample_rate / unsupported_time_scale / input_too_short`）。
可用 `X-Request-ID` 头指定请求标识，否则服务端生成；标识随响应头回显并写入日志。

## 固定算法规则（不可运行时配置）

- 分析步长 `Ha = round(sr * 10 ms)`；合成步长 `Hs = round(Ha * time_scale)`。
  **目标长度由 Ha/Hs 决定**：`T = round(N * time_scale)`，输出恰好 T 个样本。
- 窗：Hann，`L = 4 * Ha`（输入每样本被 4 帧覆盖；在支持范围内输出重叠
  `L - Hs` 始终 ≥ 50% 窗长）。
- 局部搜索：偏移 `delta ∈ [-Ha, +Ha]`，以输出重叠区（`L - Hs` 个样本）的
  归一化互相关打分。
- **平局规则（静音/周期信号有多个最优偏移时）**：得分在 `TIE_TOL=1e-9`
  内并列的候选中，取 `|delta|` 最小者；仍并列取位置较小者（偏负）。
  静音使相关无定义，所有候选记 0 分，因此确定性地落到 `delta = 0`。

## 边界语义

- **末尾长度与补偿**：帧数 `K = ceil((T - L) / Hs) + 1` 覆盖 T；末帧超出
  T 的部分被裁掉。当 `k*Ha` 越过输入尾部时，自然分析位置钳制到 `N - L`，
  搜索半径相应收缩（`notes` 中报告 `end_clamped_frames`），输出长度仍精确为 T。
- 首个输出样本为 0（Hann 窗端点为 0），归一化按窗和校正，中段增益均匀。
- 输入不足一个窗长、非有限样本、空输入、采样率越界：按上述类别拒绝。
- **支持范围**：`time_scale ∈ [0.5, 2.0]`，`sample_rate ∈ [8000, 48000]`，
  单声道。范围之外的请求被拒绝而非尝试。

## 诊断判定

每次请求记录 `request_id`、判定与关键状态（样本数、帧数、平局帧数、钳制
帧数；`input_label` 只记 SHA-256 截断哈希，样本数据不入日志）：

- `accepted`：支持范围内，无附注；
- `accepted_with_notes`：发生平局裁决或末尾钳制（附注说明数量与规则）；
- `undecidable`：输入为静音，相关搜索不携带信号信息，偏移是确定性默认值
  （0），不应解读为“匹配”；
- `rejected`：契约违例，附 `failure_category`。

## 实测失真与支持范围（scripts/verify.py，本机实测）

| 场景 | 速度因子 | 输出长度 | 接缝比(上限3.0) | 主频/周期 | 失真 |
|---|---|---|---|---|---|
| 440 Hz 纯音 | 0.5 / 0.8 / 1.3 / 2.0 | 精确 | 1.00 | 440.0 Hz（±0.02） | 残差 SNR 23.9–259.7 dB（最差在 1.3x） |
| 100 Hz 脉冲列 | 0.5 / 0.8 / 1.3 / 2.0 | 精确 | 1.00 | 中位间隔 160 样本（=输入周期） | — |
| 种子噪声 | 0.5 / 0.8 / 1.3 / 2.0 | 精确 | ≤1.28 | — | — |
| 静音 | 1.5 | 精确 | 0 | 输出全零，判定 undecidable | — |

测试阈值留有裕量：主频容差 ±5 Hz、纯音残差 SNR ≥ 20 dB、接缝比 < 3.0、
脉冲周期容差 ±4 样本。参考值来自闭式计算（最小二乘正弦拟合、FFT 峰值、
峰值检测、规格公式），并非由被测核心自身生成。

## 未执行的检查（不记为通过）

- 任意真实音频的主观听感/MOS 评估（不承诺无伪影）；
- 多声道/交织立体声输入（契约为单声道）；
- 长文件内存画像与延迟基准；
- `[0.5, 2.0]` 之外速度因子的质量评估（契约直接拒绝）。
