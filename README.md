# FIR 估计后端（正则最小二乘）

已知激励 `x[n]` 与测得响应 `y[n]`，估计有限脉冲响应（FIR）系数 `h`，模型为
`y[n] = Σ_{k=0..L-1} h[k]·x[n-k] + e[n]`，求解岭回归（Tikhonov）正规方程
`(XᵀX + λI)h = Xᵀy`。技术栈：Python 3.12 / FastAPI / NumPy / SciPy，全部数据为本地合成夹具。

## 安装与运行

```bash
pip install -r requirements.txt          # 运行时依赖（已锁定版本）
pip install -r requirements-dev.txt      # 测试依赖（pytest、httpx）

uvicorn app.main:app --port 8000         # 启动服务
python3 -m pytest                        # 运行独立测试（51 个）
```

## 示例调用

```bash
# 一次性估计（examples/estimate_request.json 由已知 FIR h=[1.0,-0.5,0.25] 真实生成）
curl -s -X POST http://127.0.0.1:8000/v1/fir/estimate \
  -H 'Content-Type: application/json' -d @examples/estimate_request.json

# 完整示例客户端：合成白噪声激励 + 延迟 5 + 噪声，自动估计延迟并对比真值
FIR_BASE_URL=http://127.0.0.1:8000 python3 examples/example_client.py

# 流式会话：分块上传后统一估计
curl -s -X POST http://127.0.0.1:8000/v1/sessions                       # → session_id
curl -s -X POST http://127.0.0.1:8000/v1/sessions/<id>/chunks -d '{"excitation":[...],"response":[...]}'
curl -s -X POST http://127.0.0.1:8000/v1/sessions/<id>/finalize -d '{"model_order":6}'
```

实测输出（本仓库交付前真实执行）：示例客户端恢复系数误差 < 6e-4（噪声 std=0.02），
估计延迟 = 真实延迟 5，holdout RMSE 0.0202 ≈ 噪声底；`estimate_request.json`
无噪声情形恢复 `h=[1.0,-0.5,0.25]` 误差 < 1e-9。

## 接口

| 端点 | 说明 |
|---|---|
| `GET /health` | 健康检查 |
| `POST /v1/fir/estimate` | 一次性 FIR 估计 |
| `POST /v1/sessions` | 创建流式上传会话 |
| `POST /v1/sessions/{id}/chunks` | 追加信号块（仅 OPEN 状态） |
| `POST /v1/sessions/{id}/finalize` | 关闭会话并对拼接信号估计 |
| `GET /v1/sessions/{id}` | 查询会话状态 |

关键请求参数：`model_order`（模型阶数，显式）、`delay` / `estimate_delay`
（时间对齐显式给出或用互相关启发式估计，来源随响应返回）、`boundary`
（`valid`/`zero_pad`）、`regularization`（λ≥0）、`holdout_fraction`
（留出比例，按时间连续切分，不打乱）、`require_identifiable`
（秩亏时直接失败而非带警告返回）。

响应包含：系数、训练误差与**留出预测误差**（分开报告）、可辨识性诊断
（奇异值、数值秩、条件数、所用容差）、实际使用的延迟及其来源、警告列表、
`run_id`（响应头 `X-Run-Id` 同步返回）。

## 设计要点

- **边界一致性**：卷积（Toeplitz）矩阵与观测向量由
  `build_design_and_observation` 成对构造，行数恒等成立（构造上保证）：
  `valid` 模式为 `(N-L+1, L)` 对应 `y[L-1:]`；`zero_pad` 模式为 `(N, L)`
  对应完整 `y`（假设 n<0 时 x=0）。
- **可辨识性**：对训练矩阵做 SVD，数值秩容差 `s_max·max(M,L)·eps`；
  激励频谱退化（如单正弦，秩=2）时报告 `identifiable=false` 并给警告，
  `require_identifiable=true` 时返回 422 `IDENTIFIABILITY_FAILURE`。
- **不用输出拟合输出**：回归矩阵只由激励构造；激励与响应完全相同
  （会退化为恒等拟合）时直接拒绝（`excitation_equals_response`）。
- **训练/留出分离**：按时间连续切分，拟合只用训练行，留出误差是真实的
  外推预测误差。
- **流状态机**：`OPEN → FINALIZED`；重复 finalize、finalize 后追加、
  空会话 finalize、未知会话均为 409 `STATE_CONFLICT`（reason 可区分）。

## 错误分类（可区分）

| HTTP | code | 含义 | 示例 reason |
|---|---|---|---|
| 400 | `INPUT_VALIDATION` | 输入契约违反 | `length_mismatch`、`response_non_finite`、`excitation_equals_response` |
| 409 | `STATE_CONFLICT` | 流状态冲突 | `already_finalized`、`session_finalized`、`session_not_found`、`no_data` |
| 413 | `RESOURCE_EXHAUSTED` | 超出配置资源上限 | `too_many_samples`、`order_too_large`、`matrix_too_large` |
| 422 | `IDENTIFIABILITY_FAILURE` | 激励秩亏不可辨识 | `rank_deficient_excitation` |
| 500 | `COMPUTATION_FAILURE` | 数值计算失败 | `normal_equations_singular`（λ=0 且秩亏）、`non_finite_solution` |

## 运行日志（可重放证据）

每次估计分配 `run_id`，关键中间状态与判断理由以 JSONL 追加到
`logs/runs.jsonl`（目录由 `FIR_LOG_DIR` 配置）：输入校验参数、对齐延迟及
来源、矩阵形状与边界模式、切分行数、秩/条件数/容差、求解 λ 与系数范数、
训练/留出 MSE、错误类别与原因。测试 `test_run_log_records_replay_evidence`
断言这些事件齐全。

## 模块边界

```
app/
  schemas.py               # 样本契约（pydantic 请求/响应）
  errors.py                # 错误分类（跨模块统一错误契约）
  config.py                # 资源上限等配置（FIR_* 环境变量）
  runlog.py                # run_id 结构化运行日志
  main.py                  # HTTP 边界、错误→状态码映射
  signal_processing/
    convolution.py         # 边界一致卷积矩阵
    alignment.py           # 显式/启发式时间对齐
    identifiability.py     # SVD 数值秩与条件数
    estimator.py           # 流水线编排 + 岭回归求解
  state/store.py           # 流会话状态机
tests/                     # 独立测试（参考答案不来自被测核心）
examples/                  # 示例请求 JSON 与客户端
```

## 测试与证据

51 个测试覆盖：卷积矩阵对照显式双重循环与 `scipy.signal.convolve`；
已知 FIR 无噪恢复（<1e-6）与含噪恢复（留出误差≈噪声方差）；单/双正弦
激励的解析秩（2/4）；正则化单调收缩与秩亏时 λ=0 的计算失败；延迟错位
退化与显式/估计延迟恢复；全部错误类别；流状态机全部非法转移；运行日志
事件完整性。参考答案来自已知真值、解析秩事实、显式循环与 SciPy 独立
实现，不由被测核心生成。

## 已知限制

- 延迟估计为互相关峰值启发式：对白噪声激励且主抽头在 index 0 的通道精确；
  有色激励或主抽头靠后的通道可能偏置（因此延迟来源始终随响应报告，
  生产使用建议显式传 `delay`）。
- 求解器为稠密正规方程，复杂度 O(N·L²)；超长信号/高阶数受
  `max_matrix_cells` 限制（默认 2e7），未实现 FFT 加速或在线递推。
- 单通道 SISO；会话存储为进程内存，重启即丢失。
- 正则参数需调用方选择，未内置交叉验证自动选 λ（留出误差可用于手动扫描）。
