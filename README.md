# pyramid-service

本地大图多分辨率金字塔 + 区域查询服务。Python / FastAPI / NumPy / SciPy / Pillow。

## 设计契约

**像素契约**：内部一律 `float64`、`(H, W, C)`（C=1 或 3），值域标称 `[0, 1]`。

**层级几何**：第 `L` 层尺寸为 `ceil(W / 2^L) × ceil(H / 2^L)` —— 奇数尺寸向上取整，
边界行列永不丢失。像素中心映射固定：第 `L` 层像素 `(x, y)` 的中心对应原图坐标
`((x+0.5)·2^L − 0.5, (y+0.5)·2^L − 0.5)`，各层一致（奇数尺寸时末像素中心可略超出
原图末像素中心，边缘块按实际覆盖重新归一化）。

**降采样内核**（`pyramid_service/kernel.py`）：

- `area`（默认）：精确面积平均（盒式抗混叠），尺度严格为 2；
- `gaussian`：显式抗混叠 —— 高斯预滤波（`scipy.ndimage.gaussian_filter`）后在固定
  中心 `2d+0.5` 处双线性采样。

**分块与原子发布**：每层切分为 `tile_size` 瓦片（边缘裁剪不填充）。瓦片先写入
`levels/.tmp-<level>-<run_id>/` 暂存目录，清单（含每瓦片 sha256、形状、dtype）写完后
用一次 `os.rename` 原子发布为 `levels/<level>/`。读者只能看到"完整已发布"或"不存在"，
重复发布报状态冲突（409）；瓦片数量不足一层时拒绝发布。构建中途失败会整体回滚
（删除未完成的金字塔目录并记录 `build_rolled_back` 日志），同一 id 可安全重试。
读取时逐瓦片校验 sha256 + 形状 + dtype，损坏瓦片抛
`TileIntegrityError`（500/compute_failure），**绝不**用全黑图冒充成功。

**区域查询**：跨瓦片区域按需读取相交瓦片并精确拼接；越界部分裁剪，完全越界或非法
参数报输入错误（400）。

**错误分类**（API 响应体 `error.category` 与 HTTP 状态一一对应）：

| category | HTTP | 含义 |
|---|---|---|
| `input_error` | 400 | 非法参数（区域、模式名、内核名…） |
| `not_found` | 404 | 金字塔/层级/瓦片不存在 |
| `state_conflict` | 409 | 重复发布金字塔或层级 |
| `resource_exhausted` | 507 | 超出像素资源上限 |
| `compute_failure` | 500 | 数值/完整性失败（含瓦片损坏） |

**日志**：每次构建/请求分配 `run_id`，JSON 行记录关键中间状态（层级尺寸、瓦片数、
校验和）与判断理由（如 `auto levels to 1x1`），可按 run_id 重放问题。

**安全边界**：金字塔 id 经白名单校验（防路径穿越）；`npy` 源路径面向本地可信使用，
未做白名单 —— 暴露到不可信网络前应加路径约束与鉴权。

## 模块边界

```
pyramid_service/
  contracts.py      数据契约（RegionSpec / TileRecord / LevelMeta、像素与瓦片约定）
  coords.py         层级几何与固定像素中心映射（纯函数）
  kernel.py         数值内核：area / gaussian 2x 降采样
  store.py          瓦片存储：原子发布、完整性校验读取
  region.py         跨瓦片区域拼接读取
  builder.py        分块构建作业（逐瓦片计算，经 store 读取上一层）
  patterns.py       合成图源（棋盘、斜线、渐变、噪声）
  api.py            FastAPI：build / levels / region / validate
  errors.py         可区分的错误分类
  observability.py  run_id JSON 日志
  config.py         配置（环境变量可覆盖）
```

## 环境与启动

依赖版本见 `requirements.txt`（开发环境 Python 3.12.3）：

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

启动服务（存储目录等可用环境变量覆盖）：

```bash
PYRAMID_STORE_DIR=./pyramid_store \
PYRAMID_MAX_REGION_PIXELS=64000000 \
uvicorn pyramid_service.api:app --port 8000
```

## 请求样例

构建 65×33 棋盘金字塔（tile 16，4 层，area 内核）：

```bash
curl -s -X POST localhost:8000/pyramids -H 'content-type: application/json' -d '{
  "pyramid_id": "demo",
  "source": {"kind": "synthetic", "pattern": "checkerboard", "width": 65, "height": 33},
  "tile_size": 16, "levels": 4, "kernel": "area"
}'
# => {"pyramid_id":"demo","run_id":"...","levels":[{"level":0,"width":65,"height":33,...}, ...]}
```

层级元数据：

```bash
curl -s localhost:8000/pyramids/demo/levels
```

跨瓦片区域（level=1，原样 float64 `.npy` 返回；`format=png` 返回 PNG）：

```bash
curl -s 'localhost:8000/pyramids/demo/region?level=1&x=14&y=3&w=8&h=6&format=npy' -o region.npy
python3 -c "import numpy as np; a=np.load('region.npy'); print(a.shape, a.min(), a.max())"
```

完整性校验（重新核对全部瓦片 sha256）：

```bash
curl -s 'localhost:8000/pyramids/demo/validate'
```

错误样例（分类可区分）：

```bash
curl -s 'localhost:8000/pyramids/demo/region?level=99&x=0&y=0&w=4&h=4'   # 404 not_found
curl -s 'localhost:8000/pyramids/demo/region?level=0&x=0&y=0&w=0&h=4'    # 400 input_error
curl -s -X POST localhost:8000/pyramids -H 'content-type: application/json' \
  -d '{"pyramid_id":"demo","source":{"kind":"synthetic","pattern":"checkerboard","width":8,"height":8}}'
# 409 state_conflict
```

## 测试

```bash
python3 -m pytest -q
```

测试要点（`tests/`）：

- 棋盘 / 斜线 / 奇数尺寸（63×47、7×5、3×5）合成图核验层间映射与内核；
- 参考答案来源独立：手算字面值、测试内朴素循环重实现、Pillow BOX（第三方面积
  重采样）三方互证，不由被测内核单独生成；
- 跨瓦片拼接与整层参考区域逐像素比较（area 与 gaussian 两种内核）；
- 覆盖层缺失（404）、重复发布（409）、资源耗尽（507）、损坏瓦片（500 且
  validate 报告坏瓦片，绝不返回全黑）、发布中途崩溃不留半成品层级；
- 日志断言 run_id 与判断理由落盘。

验收复现记录见 [ACCEPTANCE.md](ACCEPTANCE.md)。
