# Partitioned-FFT Convolution Backend

纯后端服务：对**长脉冲响应（IR）**与**分块音频流**做低延迟卷积，核心算法为
**均匀分区 FFT 卷积（uniformly partitioned overlap-add）**。技术栈：Python +
FastAPI + NumPy + SciPy。所有数据均为本地合成夹具，无生产账号、无真实业务数据。

## 目录结构

```
app/
  convolution/
    engine.py       # DSP 核心：分区 FFT + 频域延迟线(FDL) + overlap-add + 尾部冲刷
    swap.py         # IR 更换策略：restart / crossfade
  stream.py         # 流状态机：块对齐、非整块末尾、flush 语义、输出长度记账
  sessions.py       # 进程内会话注册表
  contracts.py      # 样本契约（Pydantic 请求/响应模型）
  routes.py         # HTTP 路由（薄层）
  main.py           # 应用工厂、请求 ID 中间件、错误映射
  config.py         # 环境变量配置
  diagnostics.py    # 请求标识、决策日志（accepted/rejected/undecidable）、脱敏
  fixtures.py       # 确定性合成夹具（冲激/随机/指数衰减 IR/低通 IR/超长 IR）
  errors.py         # 领域错误分类（稳定的失败类别码）
tests/
  reference.py      # 独立参考答案：纯 Python 直卷积 / numpy.convolve / scipy.fftconvolve
  test_engine_numerics.py  # 数值正确性（对照独立参考）
  test_stream.py           # 流边界语义与失败类别
  test_ir_swap.py          # IR 更换策略
  test_api.py              # 端到端 API 测试
scripts/
  verify.py         # 独立验证脚本（可对引擎或运行中的服务器执行）
  make_fixtures.py  # 导出夹具到 fixtures/*.npz
requirements.txt    # 固定版本依赖
```

## 快速开始

```bash
pip install -r requirements.txt
python -m pytest                 # 44 项测试
python scripts/verify.py         # 引擎级验证电池（9 项）
python -m uvicorn app.main:app --port 8000
python scripts/verify.py --url http://127.0.0.1:8000   # 对运行中的服务器验证
python scripts/make_fixtures.py  # 可选：导出 .npz 夹具
```

## 算法与状态

- IR 长度 `L` 被切成 `P = ceil(L/B)` 个长度为 `B` 的分区，零填充到 `N = 2B` 后
  预先做 rFFT，得到 `ir_spectra`（形状 `(P, B+1)`，complex128）。
- 每个输入块零填充到 `2B` 做 rFFT，压入频域延迟线（FDL）；输出谱为
  `sum_p FDL[p] * ir_spectra[p]`，逆变换得 `2B` 个时域样本，前 `B` 个与上一块的
  重叠尾部相加输出（overlap-add）。
- 因 `2B >= B + B - 1`，循环卷积等于线性卷积，输出是采样级精确的线性卷积
  （float64 舍入误差内，实测 max_err ≈ 1e-13）。
- **尾部冲刷**：输入结束后送入 `P` 个零块，使缓存的输入谱传播过全部分区，
  再排出残余 overlap 尾部；会话层截断到精确的应得尾长。
- **状态预算**包含完整频谱历史：`ir_spectra` 与 `input_fdl` 各为
  `P*(B+1)*16` 字节，加 `B*8` 字节的 overlap 尾。创建会话与更换 IR 时都会做
  预算投影，超限以 `STATE_BUDGET_EXCEEDED`（413）拒绝。跨淡期间新旧两代
  卷积器并存，预算按两代之和计算。

## 边界语义（务必阅读）

1. **块对齐**：除最后一块外，每块必须恰好 `block_size` 个样本（`block_size`
   须为 ≥ 8 的 2 的幂）。短块只有在 `final=true` 时合法，内部零填充对齐。
2. **final 之后**：接受 final 块后唯一合法的下一步是 `flush`；继续推块以
   `INPUT_AFTER_FINAL_BLOCK`（409）拒绝。
3. **flush 一次且仅一次**：返回当前 IR 代次应得的尾部（最后真实输入样本之后
   的 `ir_length - 1` 个样本）。无 swap 时，会话总输出长度恒为
   `总输入 + ir_length - 1`。重复 flush 以 `SESSION_ALREADY_FLUSHED`（409）拒绝。
4. **restart 更换 IR**：硬切换。旧卷积器（含频谱历史与未排出尾部）立即丢弃，
   新卷积器冷启动。切换点之后等价于用新 IR 对后续输入做全新卷积（有测试断言）。
5. **crossfade 更换 IR**：新旧卷积器并行 `crossfade_blocks` 个块，线性斜坡淡出
   旧输出、淡入新输出。过渡区**不是**采样级精确的线性卷积（新卷积器冷启动，
   缺少切换点之前的输入历史；旧 IR 超出淡窗的尾部被截断）。若切换前后有
   ≥ `ir_length` 的静音保护段且淡窗 ≥ 分区数，则跨淡对相同 IR 是采样级透明的
   （有测试断言）。无论是否处于淡窗，块进块出契约（每块进 B 出 B）始终成立。
6. **传输格式**：JSON float 数组（float64 往返）。这是夹具级传输，面向本地
   合成负载与可复现测试，不是生产音频吞吐形态；边界语义与传输格式无关。

## 诊断

- 每个请求携带 `X-Request-ID`（客户端可指定，否则自动生成），响应头与响应体
  均回显；所有日志行带同一 ID。
- 每次决策以 `decision=accepted|rejected|undecidable` 记录原因与关键状态
  （块大小、IR 长度、已处理块数、预算投影等）。`undecidable` 用于会话不存在
  等无法评估的情形。
- 样本负载属敏感/大体积数据：日志只打印长度与截断 SHA-1 摘要，绝不打印样本本身。
- 失败类别（稳定、可断言）：`SESSION_NOT_FOUND`(404)、`BLOCK_SIZE_MISMATCH`(409)、
  `EMPTY_BLOCK`(422)、`INVALID_SAMPLES`(422)、`INVALID_IR`(422)、
  `INVALID_BLOCK_SIZE`(422)、`INPUT_AFTER_FINAL_BLOCK`(409)、
  `SESSION_ALREADY_FLUSHED`(409)、`STATE_BUDGET_EXCEEDED`(413)、
  `SESSION_LIMIT_REACHED`(429)、`SCHEMA_VALIDATION`(422)。

## 验证方式

参考答案**不**由被测核心生成：`tests/reference.py` 提供纯 Python O(n·m) 直卷积、
NumPy C 直卷积（`numpy.convolve`）、SciPy 独立 FFT 实现（`fftconvolve`）三方对照。
覆盖：冲激→IR 恒等、随机信号、非整块末尾（3·256+37）、超长 IR（10 万 tap）、
块大小不变性（64/128/256/1024 两两一致）、尾部完整冲刷（末 L−1 个样本非零且
与参考一致）、流式分块与一次性卷积一致、restart/crossfade 语义、预算强制。

## 未执行 / 无法在本环境执行的检查（明确单列，不视为已通过）

- **实时音频设备接入**：本服务为纯后端，无声卡/实时线程；未做硬实时（xrun）
  验证。
- **性能基准**：延迟与吞吐只做了功能正确性验证，未做硬性性能断言（分区 FFT
  的复杂度优势是算法层面的，未在本环境跑基准）。
- **部署态端到端**：TLS、鉴权、多实例、持久化均未覆盖（本地夹具定位）。
- **多声道/多通道**：当前契约与测试均为单声道。
- **二进制流传输**：仅 JSON 浮点数组；WebSocket/二进制分块未实现。
- **模糊/性质测试**：未接入 hypothesis 等随机化测试框架；随机用例为固定种子。
