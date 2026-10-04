# 二值栅格精确欧氏距离变换服务

对二值栅格（源/非源像元）计算**精确欧氏距离变换 (EDT)**，同时返回每个像元的
**最近源点坐标**。支持各向异性像元间距、同距稳定裁决、空源/全源显式语义，
以及超大栅格的带晕环 (halo) 分块执行。

技术栈：Python 3.12 · FastAPI · NumPy · SciPy · Pillow · pytest。

---

## 1. 它如何保证“精确”，而不是看起来对

### 1.1 核心算法：可验证的分离维度抛物线下包络

`app/kernel.py` 实现 Felzenszwalb & Huttenlocher (2012) 的
**squared-distance 抛物线下包络**算法：

- 沿 x、y 两个维度各做一遍 O(n) 的 1D 下包络扫描，整体 O(H·W)；
- 每遍显式维护抛物线中心栈 `v[]` 与交点栈 `z[]`（不是 BFS、不是 chamfer、
  更不是曼哈顿距离）；
- 物理坐标直接进入抛物线：间距 `sy/sx` 以 `a²` 系数参与交点与求值，
  非方形像元是算法的一阶情形，而非后处理缩放。

### 1.2 各向异性像元间距

请求中的 `spacing_y`、`spacing_x` 为像元物理尺寸（任意正值，可不等）。
距离 = `sqrt((dy·sy)² + (dx·sx)²)`。

### 1.3 同距 (tie) 的稳定选取

当两个或以上源点到某像元的距离在 `rtol=1e-12`（配合按像元对角长度缩放的
绝对下限）内不可区分时：

- **确定地**返回字典序最小的源点 `(row, column)`；
- 该像元在结果的 `ties` 掩码中置位；
- HTTP 响应在 `warnings` 中单列 `EQUI_DISTANT_TIE`（不确定结论与失败原因
  分开，见 §5）。

实现细节：两遍扫描中，标签以 `row*W+col` 的字典序整数携带；求值阶段对
“交点恰落在整数像元上”的情形做显式双抛物线比较（交点检测窗口按
`4·eps·n²` 的舍入界设置，封顶 0.25 像元），不会因浮点舍入漏掉跨遍 tie。

### 1.4 无源点 / 全源点：两个独立定义

| 情形 | 距离 | 最近源坐标 | `has_sources` | HTTP 状态 |
|---|---|---|---|---|
| 无源点 | 全部 `+inf`（JSON 中为 `null`） | 全部 `-1` | `false` | **200 成功** |
| 全源点 | 全部 `0` | 像元指向自身 | `true`，`all_sources=true` | 200 成功 |

空源是合法结果，不是错误，也不会被静默写成 0。

### 1.5 超大栅格分块：为什么不会漏掉跨块最近点

朴素“每块各自算 EDT”在块边界处必然出错。`app/tiling.py` 使用
**按上界自适应的 halo**：

1. 用 `scipy.ndimage.distance_transform_cdt`（taxicab）求每像元到源的
   曼哈顿**步数** M(p)。恒等式
   `min(sy,sx)·M(p) ≤ euclid(p,S) ≤ √(sy²+sx²)·M(p)`
   给出每块内真实欧氏距离的上界 `D_max`（仅作界，绝不作为输出距离）。
2. 块窗口在每个方向外扩 `⌊D_max/pitch⌋+1` 个像元（**闭区间**：恰好等距的
   边界源也包含在内，否则 tie 裁决可能选到窗外字典序更小的源）。
3. 内部像元到窗口边缘 ≥ D_max，窗外源严格更远 ⇒ 窗内最近源即全局最近源。
4. 各块内部区域恰好覆盖全图一次；halo 需要多大就多大（需要时窗口退化为
   全图），正确性优先于内存。

输出距离永远来自精确内核，曼哈顿只用于 halo 定界。

---

## 2. 模块结构（职责分离）

```
app/
  contracts.py   数据契约：请求/响应模型、错误码枚举
  io_image.py    PNG(base64) 解码为源掩膜；float32/int32 结果经 RGBA-PNG 无损编码
  kernel.py      数值内核：Felzenszwalb 下包络 EDT + tie 标记 + 输入校验
  tiling.py      分块作业：Manhattan 上界、halo 规划、逐块缝合
  reference.py   独立穷举参考（纯 Python 双重循环，供核验，不与核心共享代码）
  service.py     编排：解码→校验→选路→统计；结构化 JSON 日志（关联 request_id）
  api.py         FastAPI：/health、/v1/edt、/v1/edt/verify
  config.py      环境变量配置（阈值/限额/间距范围）
  main.py        服务入口
tests/           独立测试（见 §6）
scripts/demo.py  本地合成夹具演示（进程内或 HTTP）
```

---

## 3. 运行

依赖（环境已具备则可跳过安装）：

```bash
pip install -r requirements.txt
```

启动服务：

```bash
python -m app.main
# 或：uvicorn app.main:app --host 127.0.0.1 --port 8000
# 可用环境变量：EDT_HOST / EDT_PORT / EDT_TILE_THRESHOLD / EDT_TILE_TARGET /
#               EDT_MAX_EDGE / EDT_MAX_CELLS / EDT_MIN_SPACING / EDT_MAX_SPACING
```

健康检查：

```bash
curl -s http://127.0.0.1:8000/health
```

本地演示（默认进程内 TestClient，无需起服务；`--url` 走真实 HTTP）：

```bash
python scripts/demo.py
python scripts/demo.py --url http://127.0.0.1:8000
```

演示夹具全部本地合成：精确同距、非方形像元 (sy=2, sx=0.5)、1×41 长细图、
空源、全源、强制分块的稀疏大图；每个用例打印具体距离、最近源坐标、
失败/警告类别与 `request_id`。

### 请求示例

`POST /v1/edt`

```json
{
  "image_base64": "<base64 编码的 PNG；非黑/非零像元为源>",
  "spacing_y": 1.0,
  "spacing_x": 1.0,
  "source_value": "nonzero",
  "force_tiled": false,
  "request_id": "可选的调用方关联ID"
}
```

- `source_value`: `nonzero`（默认，灰度>0）/ `white`（≥128）/ `black`（<128）
- 输出：`distance_png_base64`（float32 原始机器字封装在 RGBA-PNG，无损，
  含 `+inf`）、`nearest_y_png_base64`、`nearest_x_png_b64`（int32）；
  ≤10 000 像元时额外给出 `distance_grid` / `nearest_y_grid` /
  `nearest_x_grid`（`distance_grid` 中 `null` = `+inf`）。

`POST /v1/edt/verify`：额外对 ≤10 000 像元的栅格运行
**独立穷举参考**逐像元比对，结果在 `execution.verification`；
大图返回 `VERIFICATION_SKIPPED` 警告。

---

## 4. 可解释性

- 每个请求带 `request_id`（调用方提供或服务端生成），响应原样回显；
- 日志为单行 JSON（stderr），含服务/内核版本、`request_id`、事件
  （`request_received` / `edt_direct|edt_tiled` / `request_completed` /
  失败事件）、行列数、分块数、halo 上界、耗时、tie 像元数；
- `execution.steps` 给出 decode / validate / edt 各步毫秒耗时，
  `execution.path` 标明 direct/tiled，`execution.tiles` 与
  `manhattan_distance_bound` 说明分块位置与依据；
- `kernel_version` 随响应和日志输出。

---

## 5. 错误语义（失败原因单列）

响应顶层 `failures[]`（每项含 `code` / `message` / `location`），
与 `warnings[]` 严格分开：

| HTTP | code | 含义与定位 |
|---|---|---|
| 400 | `INVALID_IMAGE` | base64 或 PNG 无法解码；`location=body.image_base64` / `image.decode` |
| 422 | （pydantic） | 请求字段约束失败，如 `spacing<=0` |
| 400 | `INVALID_SPACING` | 间距超出配置允许范围 |
| 400 | `INVALID_SHAPE` / `EMPTY_RASTER` | 维度/空尺寸问题 |
| 413 | `TOO_LARGE` | 边长或总像元数超限（`EDT_MAX_EDGE` / `EDT_MAX_CELLS`） |
| 200 | `failures[].code=VERIFICATION_*` | 仅 verify 端点：与独立参考不一致（距离或最近源坐标），附前 5 个出错像元 |
| 500 | `INTERNAL_ERROR` | 服务端异常，回显 `request_id` 用于日志关联 |

警告（200 但需注意）：`EQUI_DISTANT_TIE`（同距裁决，结论在 1e-12 意义上
不确定）、`VERIFICATION_SKIPPED`（图太大未做穷举核验）、
`NON_FINITE_DISTANCE`、`DISTANCE_SCALE`。

**空源不是错误**：200 + `has_sources=false` + 全 `null` 距离 + `-1` 坐标。

---

## 6. 测试与复现

```bash
python -m pytest -q
```

测试都是独立断言具体数值/具体失败类别，不是“接口能调用”式检查：

- `tests/test_kernel_reference.py`（200 例）：内核对**独立穷举参考**
  （`app/reference.py`，纯 Python 定义式双循环，与核心零共享代码）逐像元比对
  距离（rel 1e-9）**与最近源坐标**；覆盖长细图 (1×N / N×1)、非方形像元
  (0.5×2.0、3.0×0.7 等)、手布源（单点/四角/十字/棋盘）、两种密度的随机掩膜、
  空源、全源、1D/2D tie、输入校验类别；
- `tests/test_scipy_crosscheck.py`：对 SciPy `distance_transform_edt`
  （第三个独立实现）交叉验证距离，并复核内核点名的源确实是源且距离自洽；
- `tests/test_tiling.py` / `tests/test_tiling_stress.py`：2×2 极小目标块 +
  仅四角源（halo 必须跨越十几个块）、单角源、各向异性、1×60/60×1 长细图、
  **halo 边界恰好等距**、内部区域恰好覆盖一次、对独立穷举/SciPy 的中规模
  (80×53) 随机稀疏压力测试；
- `tests/test_io_image.py`：各图像模式、阈值语义、坏 base64/非 PNG 分类、
  float32（含 `+inf`）/int32 的 RGBA-PNG 无损往返；
- `tests/test_api.py`：具体距离/坐标网格、tie 警告而非错误、空源 200+null、
  400/413/422 错误类别与 `location`、`request_id` 错误回显、强制分块路径。

最近一次执行结果：

```
285 passed
```

（以本地实际 `pytest` 输出为准。）

## 7. 设计取舍备注

- 结果坐标以**像元索引**返回，物理距离由 `spacing_*` 表达；
- tie 容差使用相对尺度（几何单位任意，绝对 epsilon 无意义），并把
  “1e-12 内不可区分”作为显式警告暴露，而不是假装唯一；
- 分块的曼哈顿步数只用于证明 halo 充分性，从不进入报告距离；
- float32 PNG 是机器字透传容器，不是可视化灰度图，因此可无损表达 `+inf`。
