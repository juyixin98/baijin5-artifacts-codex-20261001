# seamcarve-service

合成图像的最小能量接缝（seam carving）计算后端：动态规划求解受保护区域约束的
最优竖直接缝，支持连续删缝并输出**原图坐标**路径与能量。

## 模块划分

| 模块 | 职责 |
|---|---|
| `app/contracts.py` | 图像数据契约：base64 图像/掩码/保护矩形解码、统一响应信封、失败类别 |
| `app/energy.py` | 能量函数：**梯度能量**（Sobel，e1）与**前向能量**（CU/CL/CR）相互独立 |
| `app/kernel.py` | 数值内核：DP 累计 + 回溯；相邻行位移限制显式参数化；保护区不可穿越；无合法接缝抛 `NoLegalSeamError` |
| `app/carving.py` | 编排：每次删缝后**重算能量**并更新逐行原图坐标映射 |
| `app/jobs.py` | 分块作业：按 `chunk_size` 分批删缝，块间汇报进度，失败显式落状态 |
| `app/api.py` | FastAPI 校验接口：`/v1/seam`、`/v1/carve`、`/v1/jobs`、`/v1/version` |
| `app/config.py` | 独立配置（`SEAMCARVE_*` 环境变量），构造时校验 |
| `app/errors.py` | 错误分类：`INVALID_IMAGE` / `MASK_SHAPE_MISMATCH` / `INVALID_REQUEST` / `INVALID_CONFIG` / `NO_LEGAL_SEAM` / `JOB_NOT_FOUND` / `INTERNAL` |

## 关键规则

1. **位移限制显式**：`max_displacement` 约束相邻行列偏移 `|Δcol| ≤ D`；前向能量
   按定义仅支持 `D=1`（配置校验强制）。
2. **保护区不可穿越**：被保护像素能量/进入代价为 ∞；任一 DP 行全部不可达即
   拒绝（`NO_LEGAL_SEAM`，HTTP 422），绝不降级返回成功。
3. **连续删缝坐标映射**：每次删除后重算当前图像能量，逐行维护
   `col_map`（当前列 → 原图列），输出始终为原图坐标。
4. **并列路径确定性**：同等代价取最左前驱、最左终点。

## 快速开始

```bash
pip install -r requirements.txt
python scripts/make_fixtures.py     # 重新生成最小数据夹具（已随仓库提交）
python -m pytest -v                 # 81 个测试：正常 + 异常
uvicorn app.api:app --port 8000     # 启动服务
python examples/call_service.py     # 调用示例（正常 + 异常各若干）
```

## 调用示例

```bash
curl -s localhost:8000/v1/seam -H 'Content-Type: application/json' -d '{
  "image_b64": "'"$(base64 -w0 tests/fixtures/step_5x6.png)"'",
  "energy_mode": "gradient", "max_displacement": 1
}'
# -> {"ok": true, "data": {"path_original_cols": [0,0,0,0,0], "energy": 0.0, ...}}
```

更多见 `examples/call_service.py` 与 `artifacts/example_run.json`（真实运行留档）。

## 测试与留档

- `tests/reference.py`：独立穷举参考实现（非被测核心生成），小图全路径核验最优性。
- 手算期望值：Sobel 能量面、CU/CL/CR、连续删缝原图坐标序列均硬编码断言。
- 异常类别：整行保护 → `NO_LEGAL_SEAM`；坏 base64 → `INVALID_IMAGE`；
  掩码尺寸不符 → `MASK_SHAPE_MISMATCH`；前向能量 + `D≠1` → `INVALID_CONFIG`。
- 运行留档：`artifacts/test_run.log`（测试）、`artifacts/server_run.log`
  （结构化 JSON 服务日志，含 run_id / 输入 SHA-256 / 版本 / 逐缝判定依据）、
  `artifacts/example_run.json`（调用示例输出）。
