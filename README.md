# Geodesic Reconstruction Service

灰度/二值标记在掩膜约束下的**测地膨胀重建**服务：从标记 `marker` 出发，反复施加
测地膨胀 `f(x) = min(dilate(x, B), mask)` 直至不动点，得到 `R_mask(marker)`。

## 数据契约

- 输入：两个 PNG 文件字段 `marker`、`mask`(multipart/form-data)。
  - 8-bit 灰度（mode `L`)、16-bit 灰度（`I;16`）或 1-bit 位图（展开为 0/255);
  - RGB / 调色板 / 带 alpha 的 PNG **拒绝**（不静默转换）;
  - 二值图是灰度的退化情形，同一内核处理。
- 约束：`marker` 与 `mask` 同形状、整型、边长 ≤ `GDR_MAX_IMAGE_SIDE`（默认 4096)，
  且必须满足 `marker <= mask`。
  - `on_violation=reject`（默认）：违反时返回 **422** 与失败类别 `marker_exceeds_mask`;
  - `on_violation=clip`：显式裁剪为 `min(marker, mask)`，状态记为 `clipped` 并计入诊断。
- 全零标记拒绝（`empty_marker`)，形状不符拒绝（`shape_mismatch`)。

## 邻域与边界规则（固定）

- 邻域：`connectivity=8`（默认）或 `4`，全服务统一；
- 边界：**不回绕、不复制** —— 越界位置不参与邻域（等价于边界外恒为 0)，
  边界像素邻域更少。三个引擎实现同一边界规则，不动点一致。

## 数值内核（`engine=`)

| 引擎 | 说明 |
|---|---|
| `queue`（默认） | FIFO 队列传播（Vincent)，只处理值发生改进的像素 |
| `reference` | 朴素同步迭代（scipy `grey_dilation`)，作为可执行规范与对照基准 |
| `tiled` | 分块扫描：带 halo 的瓦片内队列重建，全局扫描至无变化，用于大图 |

三者收敛到同一最小不动点；测试逐元素断言等价（`tests/test_engine_equivalence.py`)。
重建满足**幂等**、**对标记与掩膜单调**、**不超掩膜且不小于标记**
(`tests/test_properties.py`)。

## 诊断

每个请求（含失败）都有 `request_id`(UUID)，出现在响应头 `X-Request-Id`、
响应体 `diagnostics` 与服务日志中。诊断只含脱敏状态：形状、dtype、违规像素数、
收敛计数（`iterations` / `queue_pops` / `changed_pixels`)、内容哈希前 12 位 ——
不含像素数据。状态机：`accepted` / `clipped` / `rejected` / `undetermined`
（无法判定，如解码失败、迭代上限）。

## 复现（从干净目录）

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # 版本已锁定，见 requirements.txt

# 测试（126 个用例；收敛记录写入 artifacts/convergence.jsonl)
.venv/bin/python -m pytest

# 启动服务
.venv/bin/uvicorn app.api:app --port 8931
```

### 请求样例

```bash
# 生成一对合成样例
.venv/bin/python - <<'EOF'
import numpy as np
from PIL import Image
mask = np.full((64, 64), 220, np.uint8); mask[20:30, 20:30] = 0
marker = np.zeros_like(mask); marker[10, 5] = 150
Image.fromarray(marker).save("marker.png"); Image.fromarray(mask).save("mask.png")
EOF

# 健康检查
curl localhost:8931/health

# 重建（JSON 信封，含 base64 PNG 与诊断）
curl -F "marker=@marker.png" -F "mask=@mask.png" \
  "localhost:8931/v1/reconstruct?engine=queue&connectivity=8"

# 重建（直接返回 PNG，诊断在 X-Diagnostics 响应头）
curl -o out.png -F "marker=@marker.png" -F "mask=@mask.png" \
  "localhost:8931/v1/reconstruct?engine=tiled&response_format=png"

# 仅校验契约（不重建）
curl -F "marker=@marker.png" -F "mask=@mask.png" localhost:8931/v1/validate
```

## 配置（环境变量）

| 变量 | 默认 | 含义 |
|---|---|---|
| `GDR_MAX_IMAGE_SIDE` | 4096 | 接受的最大边长 |
| `GDR_DEFAULT_CONNECTIVITY` | 8 | 默认邻域（4/8) |
| `GDR_DEFAULT_ON_VIOLATION` | reject | 违规默认策略（reject/clip) |
| `GDR_TILE_SIZE` | 256 | tiled 引擎瓦片边长 |
| `GDR_MAX_REFERENCE_ITERATIONS` | 0（不限） | reference 引擎迭代上限，超出报 `iteration_limit` |

## 工程结构

```
app/
  config.py        配置（环境变量）
  schemas.py       数据契约：选项、诊断、响应信封、失败类别
  imaging.py       PNG 编解码与 marker/mask 成对校验（信任边界）
  kernel/
    reference.py   朴素同步迭代参考内核（独立实现，作为对照基准）
    queue_impl.py  FIFO 队列内核 + 共享统计结构
  tiling.py        分块作业：瓦片规划与扫描式重建
  service.py       校验策略 + 引擎分派 + 诊断装配
  diagnostics.py   请求级日志（request_id，脱敏）
  api.py           FastAPI 边界：/health /v1/validate /v1/reconstruct
tests/
  fixtures.py            合成夹具：细桥、孔洞、平坦区、边界标记（期望值手工推导）
  test_kernel_correctness.py  内核 vs 手工参考值
  test_engine_equivalence.py  queue/tiled vs reference 逐元素等价 + 收敛记录
  test_properties.py          幂等、单调、不超掩膜
  test_validation.py          拒绝/裁剪策略与各失败类别
  test_api.py                 HTTP 端到端（含 400/422 类别断言）
artifacts/convergence.jsonl   测试产出的收敛步骤记录
```

## 验收记录（本仓库交付时实测）

- `pytest`:**126 passed**(Python 3.12，依赖版本见 requirements.txt);
- uvicorn 实机冒烟：`/health` 正常；queue/tiled 引擎结果一致，孔洞保持 0、
  细桥传播到 150、结果不超掩膜；marker 越界请求返回 422 +
  `marker_exceeds_mask`，日志含同一 `request_id`。
