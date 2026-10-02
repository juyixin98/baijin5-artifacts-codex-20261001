# 本地大图多分辨率金字塔与区域查询服务

从空工程搭建的本地服务：对合成大图构建多分辨率金字塔（面积加权抗混叠降采样），
按层级提供精确的区域查询。技术栈：Python 3.12、FastAPI、NumPy、Pillow（SciPy 为环境声明依赖）。

## 数据契约

- 像素网格：单通道 `float32`，形状 `(height, width)`，行优先。
- 层尺寸：第 L 层为 `ceil(size_0 / 2**L)`，奇数尺寸边界不丢行列
  （如 513×257 → 257×129 → 129×65 → 65×33）。
- 坐标映射（固定）：第 L 层像素 `(row, col)` 的中心对应原图坐标
  `((row + 0.5)·2^L − 0.5, (col + 0.5)·2^L − 0.5)`。
- 降采样核：面积（box）抗混叠。输出像素 `(i, j)` 取输入块
  `[2i, 2i+2) × [2j, 2j+2)`（裁剪到图边界）的算术平均；奇数边缘按实际覆盖像素数归一化。
- 瓦片：规则 `tile_size` 网格，边缘瓦片允许更小；区域请求跨瓦片时按校验和逐瓦片读取后精确拼接。

## 原子发布与完整性

- 每层先写入临时目录 `levels/.<L>.tmp-<uuid>/`（全部瓦片 + `meta.json`，含每瓦片 sha256），
  然后 `os.rename` 为 `levels/<L>/` —— rename 是唯一可见性提交点；
  `image.json` 在所有层级发布完成后最后原子落盘。
- 每次读瓦片都校验 sha256；校验失败抛 `IntegrityError`，返回 500 `compute_failure`，
  **绝不以全黑图冒充成功**。

## 错误分类（响应体 `error.category` 可机器区分）

| category | HTTP | 含义 | 示例 |
|---|---|---|---|
| `input_error` | 422 | 请求参数非法 | 区域越界、未知生成器 |
| `state_conflict` | 404/409 | 状态冲突 | 层级未发布、图像不存在、重复发布 |
| `resource_exhausted` | 413 | 资源耗尽 | 图像/区域像素超限 |
| `compute_failure` | 500 | 计算失败 | 瓦片校验和不匹配、元数据损坏 |

每个错误响应与响应头 `X-Run-Id` 都携带运行编号；结构化 JSON 日志
（`PYRAMID_LOG_FILE`）记录同一 `run_id` 下的关键中间状态（层尺寸、瓦片数、
校验失败详情）与判断理由（`reason` 字段），可按运行编号回放。

## 模块边界

| 模块 | 职责 |
|---|---|
| `contracts.py` | 数据契约：生成规格、层级/图像元数据、区域请求及校验 |
| `kernel.py` | 数值内核：层尺寸序列、像素中心映射、面积降采样 |
| `synth.py` | 合成输入：棋盘/斜线/渐变/噪声，纯坐标函数，窗口=整图切片 |
| `tilestore.py` | 瓦片存储：原子发布、sha256 校验、跨瓦片区域拼接 |
| `builder.py` | 分块作业：逐层逐瓦片构建，事件日志 |
| `service.py` | FastAPI 验证接口：请求校验、错误分类映射 |
| `errors.py` / `config.py` / `logging_utils.py` | 错误分类 / 配置 / 结构化日志 |

## 复现步骤（干净目录）

```bash
cd opp498/a
python3 -m venv .venv && source .venv/bin/activate   # 或使用系统 Python 3.12
pip install -r requirements.txt                       # 版本见文件，均已在 PyPI 声明

# 运行测试（34 项）
python3 -m pytest

# 启动服务（配置均可用环境变量覆盖，见下）
PYRAMID_DATA_ROOT=/tmp/pyramid-demo/data \
PYRAMID_LOG_FILE=/tmp/pyramid-demo/service.jsonl \
python3 -m uvicorn pyramid_service.service:app --port 8471
```

配置项（环境变量）：`PYRAMID_DATA_ROOT`（默认 `./data`）、`PYRAMID_TILE_SIZE`（256）、
`PYRAMID_MAX_LEVELS`（16）、`PYRAMID_MAX_IMAGE_PIXELS`、`PYRAMID_MAX_REGION_PIXELS`、
`PYRAMID_MAX_JSON_PIXELS`、`PYRAMID_LOG_FILE`。

## 请求样例

```bash
# 构建 513x257 棋盘金字塔（奇数尺寸），4 层
curl -s -X POST localhost:8471/images -H 'Content-Type: application/json' -d \
  '{"generator":"checkerboard","width":513,"height":257,"params":{"period":7},"levels":4}'

# 跨瓦片区域查询（tile_size=256，x=250..256 跨两个瓦片列），npy 精确值
curl -s "localhost:8471/images/<image_id>/region?level=1&x=250&y=60&w=7&h=69&format=npy" -o region.npy

# 小区域 JSON（适合直接断言）
curl -s "localhost:8471/images/<image_id>/region?level=0&x=0&y=0&w=4&h=4&format=json"

# 层级元数据（含每瓦片 sha256）
curl -s "localhost:8471/images/<image_id>/levels/1/meta"
```

生成器：`checkerboard`（period/high/low）、`diagonal`（period/width/high）、
`gradient`（kx/ky）、`noise`（seed）。均为纯坐标函数，任意窗口与整图切片一致。

## 测试

```bash
python3 -m pytest            # 34 项
python3 -m pytest -k kernel  # 数值内核（手工期望值 + 独立参考）
```

参考答案来源：`tests/reference.py` 中的显式双重循环实现（与被测内核的步长切片
实现相互独立）以及手工计算的硬编码数组；不由被测核心自身生成。

验收运行记录见 [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md)。
