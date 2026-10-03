# FIR Estimation Backend

已知激励与响应的有限脉冲响应（FIR）估计后端，基于正则最小二乘（ridge）。
技术栈：Python 3.12、FastAPI、NumPy、SciPy。所有验证数据均为本地合成夹具，
不依赖任何生产账号或真实业务数据。

## 模型与契约

前向模型为因果 FIR 卷积：

```
y[t] = sum_{k=0}^{L-1} h[k] * x[t-k] + noise,   t = 0 .. N-1
```

- **模型阶数显式**：`model_order`（抽头数 L）由调用方给出，不做自动推断。
- **时间对齐显式**：`delay` 为整数样本偏移（正值 = 响应滞后激励），估计前显式移除；
  `alignment.estimate_delay` 仅作诊断辅助（互相关峰值），不参与拟合。
- **边界卷积矩阵**：`convolution_matrix(x, L, "zero_pad")` 形状恰为 `(N, L)`，
  每行对应一个观测输出样本，与观测长度一致；`"valid"` 模式丢弃边界行。
- **训练/留出分离**：尾部 `holdout_fraction` 的样本不参与拟合，
  `train_rmse` 与 `holdout_rmse` 在不相交区间上分别报告。
- **不用输出拟合输出**：预测始终为「激励 ⊗ 估计系数」，
  观测响应从不作为自身的预测量。
- **可辨识性报告**：激励频谱退化时（有效秩 < 模型阶数，或条件数 > 1e10），
  结果携带 `identifiable=False` 与具体原因，而不是静默返回一组不可信的系数。

### 有效秩的说明

零填充边界下，任何 n > L 的设计矩阵形式秩恒为满秩（边界行总是贡献微量能量），
因此可辨识性按**有效秩**判定：奇异值大于 `EFFECTIVE_RANK_RTOL(=1e-2) * sv_max`
的个数。纯正弦激励只激发 2 个方向，有效秩为 2，会被正确标记为不可辨识。

## 目录结构

```
fir_backend/
  errors.py        错误分类：input / state / resource / computation
  contracts.py     样本契约与参数校验（所有入口共用）
  convolution.py   边界卷积（Toeplitz）矩阵与预测
  alignment.py     显式延迟移除与互相关延迟诊断
  estimator.py     正则最小二乘核心 + 可辨识性诊断
  stream.py        流式摄取会话状态机 OPEN->SEALED->ESTIMATED
  fixtures.py      合成夹具：clean / noisy / narrowband / delayed
  runlog.py        结构化运行日志（run_id + 中间状态 + 判断理由）
  api.py           FastAPI 表面（一次性估计 + 流式会话）
tests/             独立测试（67 个，含手算参考答案与跨求解器交叉验证）
examples/          端到端示例与 JSONL 运行日志
requirements.txt   锁定的依赖版本
```

## 安装

```bash
pip install -r requirements.txt
```

## 运行验证

```bash
python3 -m pytest                 # 67 个独立测试
python3 examples/estimate_demo.py # 端到端示例，写 examples/run_log.jsonl
```

## API 使用

```bash
uvicorn fir_backend.api:app --port 8000
```

一次性估计：

```bash
curl -s http://127.0.0.1:8000/v1/estimate \
  -H 'content-type: application/json' \
  -d '{
        "excitation": [0.0, 1.0, 0.5, -0.25, 0.125, 0.0, 0.0, 0.0],
        "response":   [0.0, 0.4, 0.5, 0.2, 0.05, 0.0, 0.0, 0.0],
        "model_order": 4,
        "delay": 0,
        "regularization": 0.001,
        "holdout_fraction": 0.25
      }'
```

响应包含 `run_id`、`coefficients` 与 `diagnostics`
（rank、effective_rank、condition_number、identifiable、
unidentifiable_reasons、train_rmse、holdout_rmse）。

流式摄取（分块上传后统一估计）：

```bash
curl -X POST http://127.0.0.1:8000/v1/streams                       # -> stream_id
curl -X POST http://127.0.0.1:8000/v1/streams/$SID/blocks -d '{"excitation": [...], "response": [...]}' -H 'content-type: application/json'
curl -X POST http://127.0.0.1:8000/v1/streams/$SID/seal
curl -X POST http://127.0.0.1:8000/v1/streams/$SID/estimate -d '{"model_order": 8}' -H 'content-type: application/json'
```

## 错误分类（可区分的失败类别）

| 类别 | HTTP | 含义 | 示例 |
|------|------|------|------|
| `input_error` | 400 | 样本/参数违反契约 | 长度不一致、NaN、非法阶数 |
| `state_conflict` | 409 | 流状态冲突 | seal 后追加、未 seal 先估计 |
| `resource_exhausted` | 413 | 超出配置上限 | 样本数/会话数超限 |
| `computation_failure` | 500 | 数值管线失败 | 求解器失败、非有限系数 |
| （未知 stream id） | 404 | 会话不存在 | — |

所有错误响应携带 `category`、`message`、`run_id`。

## 运行日志与重放

每次估计分配 `run_id`，关键中间状态按序落日志：
`inputs_validated -> alignment_applied -> split -> solve_diagnostics
-> metrics -> estimate_complete`（失败时为 `validation_failed` /
`request_failed` / `computation_failed`，含错误类别）。
日志记录秩、有效秩、条件数、奇异值、正则参数、两类误差与判断理由，
可按 `run_id` 重放（`runlog.replay`）。示例运行日志见
`examples/run_log.jsonl`（由 `examples/estimate_demo.py` 生成）。

## 测试设计（证据侧）

- **参考答案独立**：参考 FIR 为手写字面量/公式定义；响应由 `numpy.convolve`
  合成（与被测 Toeplitz 路径无关）；另用 scipy 正规方程解交叉验证估计结果；
  小尺寸卷积矩阵断言手算字面量。
- **具体断言**：无噪声恢复误差 < 1e-8；含噪声系数误差 < 0.05 且两类 RMSE
  落在噪声标准差的 [0.5, 2] 倍区间；窄带激励有效秩恰为 2 且
  `identifiable=False`；正则参数增大时系数范数严格单调下降、λ=1e12 时趋零；
  延迟错位未对齐时误差 > 0.1、显式对齐后 < 1e-8。
- **失败类别可区分**：契约违例、状态冲突、资源超限、求解器失败
  （monkeypatch 注入）分别断言到具体异常类型与 HTTP 状态码。

## 已知限制

- 仅支持单输入单输出（SISO）、实值、均匀采样信号；无多通道/复值支持。
- 延迟为整数样本；不支持亚样本对齐。
- 流式会话注册表为进程内存实现，重启即丢失，不适合多副本部署。
- 有效秩阈值（1e-2）与病态阈值（1e10）为工程经验值，
  边界情形（如有效秩恰在阈值附近）需结合奇异值谱人工判断。
- 正则参数由调用方显式给定，未实现自动选择（如 L 曲线/交叉验证）。
- 资源上限为单请求样本数与会话数，未做每客户端限流或认证
  （本地合成场景，无生产账号需求）。
