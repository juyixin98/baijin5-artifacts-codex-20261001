# Exact Euclidean Distance Transform Service

二值栅格的**精确**欧氏距离变换（EDT）后端：对每个像元返回其到最近源像元的
欧氏距离，以及最近源像元的身份（坐标）。多模块 FastAPI 服务，纯本地运行，
无需任何外部账号或真实业务数据。

## 模块划分

| 模块 | 职责 |
|---|---|
| `edt_service/contracts.py` | 图像数据契约：JSON 线格式校验、网格→掩码转换、结果编码 |
| `edt_service/kernel.py` | 数值内核：可分离一维抛物线下包络精确 EDT（含最近源标签传播） |
| `edt_service/tiling.py` | 超大栅格分块作业：精确 halo 推导，不漏跨块最近点 |
| `edt_service/service.py` | 编排：直连/分块策略选择、退化情形独立定义、版本与计时溯源 |
| `edt_service/api.py` | FastAPI 验证/执行接口、错误分类、请求关联日志 |
| `edt_service/reference.py` | 独立逐像元穷举参考实现（仅供测试/调试，与内核零共享代码） |
| `edt_service/config.py` | 配置（环境变量 `EDT_*` 可覆盖） |
| `tests/` | 独立测试：手算用例、穷举对照、SciPy 对照、API 契约 |

## 核心算法（不是曼哈顿/倒角近似）

平方欧氏距离沿坐标轴可分离：

```
D²(r,c) = min over sources (sr,sc) of ((r-sr)·dy)² + ((c-sc)·dx)²
```

内核先对每一列沿行方向做一维精确变换（Felzenszwalb–Huttenlocher 抛物线
下包络，线性时间），再对每一行沿列方向做一次。两遍均传播胜出抛物线的
索引，因此同时得到最近源身份。结果与逐像元穷举及
`scipy.ndimage.distance_transform_edt` 在测试中逐项一致。

### 像元间距各向异性

`spacing = [dy, dx]`，行/列间距独立为正有限浮点。例如 `dy=3, dx=1` 时，
`(1,1)` 到 `(0,0)` 源的距离是 `hypot(3,1)=√10`，有专门测试锁定。

### 同距源稳定选取（tie-break 契约）

距离相等时按 **`(距离, 源列号, 源行号)`** 取最小者：先比距离，再比列，
最后比行。该规则确定、可复现，内核、穷举参考、分块路径三者一致
（分块窗口是连续子网格，局部字典序与全局一致，故标签不变）。

### 退化情形（独立定义，不依赖内核偶然行为）

- **无源点**：所有距离 `+inf`（JSON 中为 `null`），所有标签 `-1`
  （JSON 中为 `null`）。响应 `mode = "degenerate-empty"`。
- **全源点**：所有距离 `0`，每个标签为自身扁平索引。
  响应 `mode = "degenerate-full"`。

### 超大栅格分块（不漏跨块最近点）

固定 halo 的分块会悄悄丢掉远处获胜源。本实现按块推导精确 halo：

1. 仅用块内源跑内核，得到每像元上界 `u(p)`，取 `U = max u(p)`；
   真实最近距离处处 `d(p) ≤ U`。
2. 任何可能获胜的源必在块包围盒外扩 `ceil(U/dy)+1` 行、
   `ceil(U/dx)+1` 列的窗口内（窗口裁剪到栅格边界）。
3. 在窗口上重跑内核并裁回本块。块内无源时改用四角点三角不等式上界
   （`d(p) ≤ 块对角线 + max角点最近距离`，角点距离对全局源表 O(S) 求得）。

测试包含"唯一源在远角"等用例，验证跨多块最近点不丢失，且分块结果与
直连、穷举三者逐像元一致（距离与标签都比对）。

## 运行

```bash
pip install -r requirements.txt

# 服务入口
uvicorn edt_service.api:app --port 8000

# 本地演示（合成掩码 → 服务 → SciPy 交叉核验 → 输出热力图）
python scripts/demo.py              # 直连模式
python scripts/demo.py --tiles 64   # 强制分块模式

# 测试（必须实际执行）
python -m pytest tests/ -q
```

## API

### `POST /v1/edt`

请求：

```json
{
  "grid": [[1, 0], [0, 0]],
  "spacing": [1.0, 1.0],
  "tile_size": null,
  "request_id": "可选，客户端关联 id"
}
```

- `grid`：非空矩形 0/1 嵌套列表，`1` = 源点。拒绝非矩形、非整型、
  非 0/1、布尔值（防止静默强转）。
- `spacing`：`[dy, dx]`，均为正有限数，缺省 `[1.0, 1.0]`。
- `tile_size`：缺省按规模自动选择；显式传入则强制分块。

响应（200）：

```json
{
  "request_id": "...",
  "mode": "direct | tiled | degenerate-empty | degenerate-full",
  "shape": [2, 2],
  "spacing": [1.0, 1.0],
  "elapsed_ms": 0.42,
  "versions": {"edt_service": "...", "numpy": "...", "scipy": "...", ...},
  "tiles": [{"tile": [r0,c0,r1,c1], "window": [...], "upper_bound": 3.2}],
  "semantics": {"tie_break": "nearest source chosen by (distance, column, row)", ...},
  "distances": [[0.0, 1.0], [1.0, 1.414...]],
  "labels": [[0, 0], [0, 0]]
}
```

- `labels` 为最近源的扁平索引 `row * width + col`；`null` 表示无源。
- `distances` 中 `null` 表示 `+inf`（全图无源）。
- `tiles` 给出每块的窗口与上界，便于审计分块行为。

### `GET /health` · `GET /v1/version`

存活探针与组件版本（版本同时内嵌在每个 EDT 响应中）。

## 错误语义

失败响应为稳定信封，HTTP 状态码 + 机器可读类别：

```json
{"error": {"category": "INVALID_GRID", "message": "...", "request_id": "..."}}
```

| category | HTTP | 含义 |
|---|---|---|
| `INVALID_GRID` | 422 | 空网格、非矩形、非 0/1、非整型、布尔值、超维度上限 |
| `INVALID_SPACING` | 422 | 间距非正、非有限、非数值 |
| `INVALID_OPTION` | 422 | `tile_size` 等非正 |
| `PAYLOAD_TOO_LARGE` | 413 | 像元总数超过 `EDT_MAX_GRID_PIXELS` |
| `INTERNAL_ERROR` | 500 | 未预期异常（日志含堆栈与 request_id） |

注意：**无源点不是错误**——它是合法输入，返回 200 与全 `null` 结果。

## 可解释性与日志

每个请求获得 `request_id`（客户端可经 body 或 `x-request-id` 头指定，
否则自动生成），出现在：响应体、响应头、以及该请求期间全部日志行
（`req=<id>`）。日志记录关键步骤：网格形状、间距、源点计数、执行模式、
分块数、耗时；失败时记录类别与原因。不确定/异常结论（如分块上界
回退到角点法）通过 `tiles[].upper_bound` 与日志可见。

## 测试策略（防"正常输入对、边界悄悄错"）

- 手算精确用例：1×N、√2 对角、各向异性 √10、三种同距 tie、空/满栅格；
- 随机对照：内核 vs 独立穷举（距离+标签，40 组随机种子 × 多种间距）；
- 第三方对照：距离 vs SciPy（注意 SciPy 对无源栅格返回到边界的距离，
  与本契约的 `inf` 语义不同，故该对照仅在有源时启用）；
- 长细图：1×300、300×1、2×500、3×97 等；
- 分块：随机图多块一致、远角单源跨块、块内无源回退、分块 tie 一致性、
  块报告全覆盖；
- API：具体数值断言 + 每类失败的 category 断言。

## 已知限制

- 内核为纯 Python 循环调度的 O(H·W) 算法，吞吐不及编译实现；分块模式
  每块需在扩展窗口上重跑，常数因子更高（正确性优先）。
- 同距判定基于浮点精确相等；间距为整数时 tie 是精确的，极端非二进制
  友好间距下浮点舍入可能使"近似同距"按微小差值定胜负（契约仍确定）。
- JSON 线格式不适合超大栅格传输；`EDT_MAX_GRID_PIXELS` 提供硬上限。
