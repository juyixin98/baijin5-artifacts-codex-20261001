# STFT / ISTFT 后端

基于 **Python + FastAPI + NumPy + SciPy** 的短时傅里叶变换（STFT）与逆变换（ISTFT）
多模块后端。核心算法为纯 NumPy 实现，SciPy 仅用于窗函数生成与测试交叉验证。

> 最关键的约束在三处保持统一：**窗函数、中心填充（center padding）、最终裁剪（trim）**
> 在分析与合成路径上完全一致；逆变换的 overlap-add 归一分母在每个输出样本上都被
> 显式检查，分母为零时明确拒绝（`UNCOVERED_SAMPLES`）。

---

## 1. 目录结构

```
stft_backend/
  errors.py          # 稳定的错误码与异常层级（失败可按类别断言）
  config.py          # 环境变量配置、服务版本与处理位置元数据
  logging_setup.py   # JSON 结构化日志，ContextVar 关联 request_id
  windows.py         # 窗函数解析（命名字符串 / 显式样本）
  numeric.py         # 参数校验、NOLA 可重构条件检查
  algorithms.py      # 批量 STFT / ISTFT 核心（纯 NumPy，单一约定来源）
  streaming.py       # 帧级流式分析器、OLA 合成器、内存会话存储
  contracts.py       # pydantic 请求/响应契约（统一响应信封）
  api.py             # FastAPI 路由、请求 ID 中间件、错误信封
tests/
  reference_oracle.py# 独立参考答案：DFT 矩阵 / 闭式常数 / SciPy 交叉验证
  test_numeric.py    # 参数与 NOLA 单元测试
  test_algorithms.py # 数值算法测试（对照独立 oracle）
  test_streaming.py  # 流状态测试
  test_api.py        # 端到端 API 集成测试
  test_logging.py    # 日志关联测试
examples/            # 示例请求 JSON 与标准库客户端
scripts/             # 本地启动 / 测试脚本
requirements.lock    # 精确依赖锁定
```

样本契约（`contracts`）、信号算法（`algorithms`）、流状态（`streaming`）、
数值测试（`tests` + `reference_oracle`）分别承担实际工作，没有硬编码演示。

---

## 2. 本地启动

需要 Python 3.11+（在 3.12.3 上验证）。

```bash
# 建议使用虚拟环境
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock

# 启动（默认 127.0.0.1:8000，带 --reload）
./scripts/run_dev.sh
# 或：python3 -m uvicorn stft_backend.api:app --host 127.0.0.1 --port 8000
```

交互式文档：<http://127.0.0.1:8000/docs>

可配置环境变量：`STFT_DEFAULT_NPERSEG`、`STFT_DEFAULT_HOP`、
`STFT_DEFAULT_WINDOW`、`STFT_LOG_LEVEL`、`STFT_MAX_SIGNAL_SAMPLES`、
`STFT_MAX_FRAMES_PER_SESSION`。

## 3. 运行测试

```bash
./scripts/run_tests.sh
# 或：python3 -m pytest -v --cov=stft_backend --cov-report=term-missing
```

86 个测试，核心包行覆盖率约 **95%**（要求 ≥ 80%）。

## 4. 示例请求

```bash
# 健康检查
curl -s http://127.0.0.1:8000/health

# 校验奇数窗长/步长是否可重构（关联请求身份）
curl -s -X POST http://127.0.0.1:8000/v1/validate \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: demo-001' \
  -d @examples/validate_request.json

# 批量往返（STFT 后立即 ISTFT，返回误差指标）
curl -s -X POST http://127.0.0.1:8000/v1/roundtrip \
  -H 'Content-Type: application/json' \
  -d @examples/roundtrip_request.json

# 标准库端到端示例（批量 + 流式）
python3 examples/example_roundtrip.py
```

### 端点一览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/health` | 健康检查 |
| GET  | `/v1/info` | 版本、默认值、限制、处理位置 |
| POST | `/v1/validate` | 仅校验参数与 NOLA 条件 |
| POST | `/v1/stft` | 批量正变换 |
| POST | `/v1/istft` | 批量逆变换 |
| POST | `/v1/roundtrip` | 正+逆变换，返回具体误差指标 |
| POST | `/v1/streams` | 创建 analyze / synthesize 会话 |
| POST | `/v1/streams/{id}/analyze` | 推送信号块（可 `finish`） |
| POST | `/v1/streams/{id}/synthesize` | 按序推送谱帧（可 `finish`） |
| DELETE | `/v1/streams/{id}` | 关闭会话 |

复数谱在线路上统一表示为 `[real, imag]` 对；响应统一为
`{success, request_id, data, error, meta}` 信封。

---

## 5. 支持范围与约定

- **实值一维信号**；`nperseg` 支持**偶数与奇数**窗长，`nfft >= nperseg`
  （可零填 FFT），`1 <= hop < nperseg`。
- **单边/双边谱**：默认单边。单边恢复完整谱时补共轭对称，
  **直流（DC）只保留一次，Nyquist 仅在偶数 nfft 时出现且不重复**；
  奇数 nfft 无 Nyquist bin。DC/Nyquist 必须为实数，否则报 `ASYMMETRIC_SPECTRUM`。
- **窗函数**：分析与合成使用同一个数组；支持 scipy 命名字符串
  （`hann`、`hamming`、`blackman`、`boxcar` 等，DFT-even/periodic 约定）
  或直接提交等长显式样本。
- **保留原始长度**：逆变换按记录的原始长度裁剪，填充永不泄漏到输出。

### 关键取舍

1. **中心填充**：信号两端各补 `nperseg // 2` 个零，再按 hop 网格对齐尾部，
   使第 0 帧的窗峰对齐样本 0。帧中心（窗峰位置）对奇/偶窗长都恰为 `m * hop`，
   与 `scipy.signal.stft(boundary='zeros')` 的时间向量一致。
2. **加权 OLA 归一分母**：逆变换除以 `sum_m |w[n - m*hop]|^2`（最小二乘意义），
   而非要求更严格的 COLA 常值和。这支持更一般的窗/步长组合。
3. **NOLA 而非 COLA**：预检用与 `scipy.signal.check_NOLA` 相同的
   “按 hop 循环折叠窗能量”构造；不满足时在变换前即明确拒绝（`NOLA_VIOLATION`），
   错误详情给出首个零分母残差位置。合成时仍对**真实分母逐样本复查**，
   请求长度超出帧覆盖范围时返回 `UNCOVERED_SAMPLES`（列出首个未覆盖样本）。
4. **流式与批量共享同一约定**：流式分析器内部持有相同的边界填充，
   合成器逐帧累加相同的 `w` 分子与 `w^2` 分母，仅在 `finish` 时归一化与裁剪，
   因此两条路径数值一致、可直接对拍。
5. **数值容差**：NOLA 零判定为 `max(1e-12, 1e-10 * 峰值)`；
   DC/Nyquist 虚部容差 `1e-8`。
6. **短于一窗的信号**：本后端借助边界填充**支持**信号短于 `nperseg`
   并精确恢复；`scipy.signal.stft`（legacy 接口）会对这种情况直接报错，
   这是有意的支持范围差异。

### 帧索引 → 样本位置

每帧返回 `frame_index`、`start_sample = m*hop - nperseg//2`（早期帧可为负，
表示伸入左侧填充）、`center_sample = m*hop`。测试用 scipy 时间向量交叉验证。

---

## 6. 错误类别（稳定错误码）

`VALIDATION_ERROR`、`INVALID_PARAMETER`、`UNSUPPORTED_WINDOW`、
`WINDOW_LENGTH_MISMATCH`、`NFFT_TOO_SMALL`、`NOLA_VIOLATION`、
`SPECTRUM_SHAPE_MISMATCH`、`ASYMMETRIC_SPECTRUM`、`NON_FINITE_SIGNAL`、
`UNCOVERED_SAMPLES`、`SESSION_NOT_FOUND`、`SESSION_DIRECTION_CONFLICT`、
`FRAME_SEQUENCE_ERROR`、`INTERNAL_ERROR`。

错误单独放在响应的 `error` 块（`code` / `message` / `stage` / `details`），
不确定或越界结论（如 `signal_length` 超出原长）在数据中以 `note` 单列。

---

## 7. 可解释性

- 每个请求可由客户端通过 `X-Request-ID` 指定，否则服务端生成；
  响应头与响应体、以及该请求产生的每条 JSON 日志都带同一 `request_id`。
- 日志记录关键步骤（`stft.start` / `stft.done` / `istft.start` /
  `stream.created` / `request.failed`）与参数；`meta.processing_location`
  与日志均含服务名、版本、主机、Python 版本。

## 8. 测试独立性说明

`tests/reference_oracle.py` **不导入**任何被测核心模块；期望值来自：

1. 显式 **DFT 矩阵**乘法（不走 FFT 库）；
2. `w=[0,1,1,0]、hop=2` 的**闭式推导**（分母恒为 1，精确重构）；
3. `scipy.signal.stft/istft/check_NOLA` 仅作约定交叉佐证，
   从不是断言的唯一依据。

测试断言具体数值（往返误差阈值、帧位置、错误码与 `details`），
而非“接口可调用”。
