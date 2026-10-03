# 复现文档

## 环境

- Python 3.12（Linux）；依赖见 `requirements.txt`（直接依赖，已固定版本）与
  `requirements-lock.txt`（含传递依赖的完整锁定，由 `pip freeze` 生成）。

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 配置

全部通过环境变量（前缀 `SEAMCARVE_`），缺省值即可运行：

| 变量 | 默认 | 说明 |
|---|---|---|
| `SEAMCARVE_ENERGY_MODE` | `gradient` | `gradient` 或 `forward` |
| `SEAMCARVE_MAX_DISPLACEMENT` | `1` | 相邻行最大列偏移；`forward` 模式必须为 1 |
| `SEAMCARVE_JOB_CHUNK_SIZE` | `4` | 分块作业每块删除的接缝数 |
| `SEAMCARVE_MIN_REMAINING_WIDTH` | `1` | 删除后图像最小剩余宽度 |
| `SEAMCARVE_LOG_LEVEL` | `INFO` | 日志级别 |

## 复现步骤

```bash
# 1. 生成最小数据夹具（确定性，已提交；此步验证可再生成）
python scripts/make_fixtures.py

# 2. 运行全部测试（正常 + 异常）
python -m pytest -v

# 3. 启动服务并运行调用示例
uvicorn app.api:app --port 8000 &
python examples/call_service.py   # 输出写入 artifacts/example_run.json
```

## 判定依据（如何核对结果）

- **最优性**：`tests/test_kernel.py::TestBruteForceOracle` 用 `tests/reference.py`
  的独立穷举枚举对随机小图（含随机保护掩码）核验 DP 能量与路径可行性。
- **手算锚点**：`tests/test_energy.py` 的 Sobel 能量面与 CU/CL/CR、
  `tests/test_carving.py` 的连续删缝原图坐标序列 `[0,1,4]`、能量 `[0,0,0]`
  均为手工推导后硬编码。
- **日志关联**：每条服务日志带 `run_id` 与 `input_sha256`（输入原字节 SHA-256），
  `run_started` 记录版本快照；`seam_selected` 记录能量、模式、位移、并列数等
  判定依据；作业日志带 `job_id` 与分块进度。样例见 `artifacts/server_run.log`。
- **异常不谎报**：领域错误返回 `"ok": false` + 稳定 `category`；未知异常返回
  500/`INTERNAL` 并带堆栈日志；作业失败状态为 `failed` 且附错误类别。
