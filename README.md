# LPC Audio Frame Backend

音频帧线性预测（LPC）后端：自相关法分析、Levinson-Durbin 求解、残差提取与
重构，FastAPI 提供 HTTP 边界。所有数据均为本地合成夹具，无外部账号与真实业
务数据。

## 模块划分（各模块承担实际工作）

| 模块 | 职责 |
|---|---|
| `app/contracts.py` | 样本契约：请求/响应 Pydantic 模型，API 边界的唯一事实来源 |
| `app/lpc/autocorr.py` | 固定窗（hann/hamming/rect）+ 有偏自相关 r[0..order] |
| `app/lpc/levinson.py` | Levinson-Durbin 递归，反射系数失稳/奇异时产生诊断 |
| `app/lpc/reference.py` | 独立参照：scipy Toeplitz 直接求解（与被测递归不共享代码） |
| `app/lpc/filters.py` | 分析 FIR A(z) / 合成 IIR 1/A(z)，初始状态闭式对应 |
| `app/verification.py` | 数值验证：Toeplitz 对照、重构误差直接判定、稳定性检查 |
| `app/stream.py` | 流状态：跨帧携带分析/合成滤波器状态 |
| `app/pipeline.py` | 编排：analyze / reconstruct / roundtrip，日志与诊断汇总 |
| `app/main.py` | FastAPI 入口：请求身份关联中间件、结构化错误、流会话端点 |
| `app/config.py` | 启动配置（环境变量 `LPC_*`，含默认值） |

## 快速开始

```bash
pip install -r requirements.txt          # fastapi, uvicorn, numpy, scipy, pytest, httpx
python scripts/generate_fixtures.py      # 重新生成本地合成夹具（确定性种子）
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

健康检查与一次往返：

```bash
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/v1/lpc/roundtrip \
  -H 'Content-Type: application/json' -H 'X-Request-ID: demo-1' \
  -d '{"samples": [0.0, 0.5, -0.5, 0.25, 0.1, -0.3, 0.2, 0.0],
       "config": {"frame_size": 8, "order": 2, "window": "hann"}}'
```

## API 概览

- `POST /v1/lpc/analyze` — 分帧分析：LPC 系数、反射系数、增益、残差、逐帧稳定性
- `POST /v1/lpc/reconstruct` — 由 (lpc, residual) 帧序列重构信号
- `POST /v1/lpc/roundtrip` — 分析+重构，直接以重构误差判定（见下）
- `POST /v1/lpc/stream/{session}/frame` / `DELETE /v1/lpc/stream/{session}` — 有状态流会话
- `GET /health`

所有响应携带 `request_id`（可用 `X-Request-ID` 头指定，否则自动生成并保持
中间件日志、响应体、响应头三者一致）、组件版本与处理位置
（`processing.version` / `processing.module`）。失败以结构化信封返回：

```json
{"request_id": "...", "version": "0.1.0",
 "error": {"category": "invalid_request|invalid_config|invalid_frame", "message": "..."}}
```

## 关键设计约定

- **方法固定**：自相关法 + 固定窗 + 固定阶数（按请求 config，校验
  `1 <= order < frame_size`，阶数达到帧长会使自相关矩阵结构性奇异，直接 422）。
- **零能量帧有定义**：LPC = [1, 0, …, 0]，增益 0，残差全零，
  `zero_energy=true`，诊断 `zero_energy_frame`。
- **失稳诊断**：任一反射系数 |k| ≥ 1（裕度 1-1e-12）→ `unstable_reflection_coefficient`；
  预测误差能量非正/非有限 → `singular_autocorrelation`，递归在断点截断并置零
  后续系数，绝不静默返回垃圾。
- **窗只用于系数估计**：残差在未加窗的原始帧上计算，分析/合成保持精确互逆。
- **初始状态对应**：分析 FIR 状态由前 `order` 个输入样本闭式构造，合成 IIR
  状态由前 `order` 个输出样本闭式构造（全极点滤波器状态完全由过去输出决定）；
  测试断言闭式状态与 `lfilter` 的 `zf` 完全相等，且流式分帧与一次性处理结果
  逐样本一致。
- **小残差 ≠ 无损**：无损判定只接受直接重构误差（`||x-x̂||/||x|| <= 1e-9`）。
  残差小但直接误差大时判定为 `uncertain` 并记录原因（典型成因：滤波器初始
  状态不对应），列入响应的 `uncertain` 字段，绝不计入 `lossless_confirmed`。
- **不确定结论单列**：Levinson 结果与独立 Toeplitz 求解逐帧对照，偏差超
  1e-8（病态正规方程的典型表现，如高阶建模单频信号）时写入 `uncertain`。

## 测试

```bash
python -m pytest                 # 全部：单元 + 集成
python -m pytest tests/unit      # 仅单元
python -m pytest tests/integration
```

最近一次运行结论（本机，Python 3.12 / numpy 2.4 / scipy 1.15）：

```
======================== 46 passed, 1 warning in 1.21s =========================
```

验证策略（参考答案均不由被测核心自身生成）：

- **AR 夹具**：已知生成系数 [1, -0.2, 0.67, 0.126, 0.3969] 的 AR(4) 过程，
  断言 LPC(order=4) 估计收敛到生成系数附近（统计容差 0.15），且往返重构
  相对误差 < 1e-9 判定 `lossless`。
- **单频夹具**：440 Hz 正弦，二阶 LPC 对照闭式解 [1, -2cos(ω), 1]（容差
  0.05，存于 `tests/fixtures/expected.json`）；高阶（40）建模时断言
  Toeplitz 对照偏差进入 `uncertain`。
- **静音夹具**：全零帧 → 零能量定义行为 + 重构精确为零。
- **阶数过高**：`order >= frame_size` → 422 `invalid_request`；秩亏自相关
  （r[k]=cos(ωk)）→ 递归给出失稳与奇异诊断。
- **Toeplitz 对照**：测试内用 `scipy.linalg.solve_toeplitz` 独立求解，
  与 API 返回系数逐项比较（良态白噪声下容差 1e-8）。
- **手算参考**：自相关 r=[30,20,11]、二阶 Levinson 递推手算值等，存于
  `expected.json` 与测试注释中。
- **状态对应**：流式两帧处理与一次性处理残差逐样本一致（atol 1e-12）；
  错误初始状态下残差不变但重构误差 > 1e-3，证明判定不依赖残差大小。

## 配置

环境变量（均有本地默认值）：`LPC_FRAME_SIZE`(256)、`LPC_ORDER`(10)、
`LPC_WINDOW`(hann)、`LPC_LOSSLESS_REL_TOL`(1e-9)、`LPC_LOG_LEVEL`(INFO)。

日志格式包含版本与请求身份，关键步骤（分帧布局、失稳帧、零能量帧、验证
结论）逐条记录：

```
2026-10-04 01:36:15 INFO [version=0.1.0 request_id=d2ddb05ecadc45cc] lpc_backend: analyze: n_samples=512 frame_size=256 order=10 window=hann -> 2 frames
2026-10-04 01:36:15 INFO [version=0.1.0 request_id=d2ddb05ecadc45cc] lpc_backend: roundtrip verdict=lossless rel_err=6.358e-17
```
