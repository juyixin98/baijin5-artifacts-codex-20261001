# skeleton-backend

二值图拓扑保持细化（Zhang-Suen 全并行同步删除）+ 骨架图提取后端。
技术栈：Python 3.12 / FastAPI / NumPy / SciPy / Pillow。所有输入均为本地合成夹具，无外部账号与真实业务数据。

## 行为契约

1. **相容邻接**：前景 8-连通、背景 4-连通。删除条件（`A==1`、`2<=B<=6`）只移除简单点，
   前景分量不分裂、背景孔洞不打开。
2. **同步删除**：每个子迭代的删除掩码只由同一前态计算，算完再统一提交；
   同一子迭代内的删除互不可见。
3. **端点与孔洞保留**：端点（`B==1`）永不删除；孔洞数在细化前后必须相等（由 `/validate` 核验）。
4. **分块不独立细化**：每个子迭代先物化共享前态的 halo，各块只读邻块当前像素计算候选，
   全部块候选收齐后同步提交；收敛判定是全局的（整轮所有块零删除才停）。
   分块结果与整图参考**逐像素相等**（测试断言）。
5. **骨架图边保存原像素链**：每条边是 `(row, col)` 坐标链，含两端节点像素。

## 模块划分

| 模块 | 职责 |
|---|---|
| `app/contracts.py` | 图像数据契约：0/1 二值校验、PNG 解码、分类化错误（`NOT_BINARY`/`TOO_SMALL`/…） |
| `app/kernel.py` | 数值内核：Zhang-Suen 两个子迭代的向量化删除掩码 + 轮次循环 |
| `app/topology.py` | 独立拓扑探针（scipy 实现，不复用内核）：连通分量、孔洞、端点、结点 |
| `app/tiles.py` | 分块作业：tile 规划、halo 交换、同步提交、全局收敛 |
| `app/graph.py` | 骨架图提取：端点/结点簇/孤立点/环节点，边存原像素链 |
| `app/service.py` | 编排：契约校验 → 细化 → 拓扑核验 → 图提取，产出可解释报告 |
| `app/main.py` | FastAPI 接口：`/health`、`/skeletonize`、`/validate`，请求身份中间件 |

## 快速开始

```bash
pip install -r requirements.txt
python3 -m pytest -q            # 27 passed
python3 scripts/make_samples.py # 生成 data/*.png 样例
uvicorn app.main:app --port 8000
```

调用示例：

```bash
# 二值 PNG（0/255）base64 上传，指定分块大小
python3 - <<'EOF'
import base64, json, urllib.request
png = base64.b64encode(open('data/fork.png','rb').read()).decode()
req = urllib.request.Request('http://127.0.0.1:8000/skeletonize',
    data=json.dumps({'image_png_base64': png, 'tile_size': 6}).encode(),
    headers={'Content-Type': 'application/json', 'X-Request-ID': 'demo-fork-1'})
body = json.loads(urllib.request.urlopen(req).read())
print(body['validation']['status'], body['validation']['metrics'])
EOF
```

也可以直接传像素数组：`{"pixels": [[0,1,...], ...], "mode": "tiled", "tile_size": 8}`。

## 实测输出（2026-10-03，本仓库代码）

`python3 -m pytest -q`：

```
27 passed, 1 warning in 0.81s
```

`POST /skeletonize`（fork.png，tiled，tile_size=6）关键字段：

```
rounds:    [{round 0: sub1=37, sub2=32}, {round 1: sub1=8, sub2=0}, {round 2: 0, 0}]
tiles:     {tile_size: 6, tile_count: 12, halo_exchanges: 6}
deleted:   77   skeleton_pixels: 26
validation: pass  failure_reasons=[]  warnings=[]
metrics:   components 1->1, holes 0->0, endpoints_skeleton=3
graph:     nodes=[junction x1, endpoint x3]  edges=3（每条边含原像素链）
tiled == full 整图参考: True
```

日志按请求关联（`req=<request_id>`），关键步骤、版本、失败原因单列：

```
INFO [req=demo-fork-1] skeleton.service: skeletonize start: shape=(20, 16) foreground=103 mode=tiled tile_size=6
INFO [req=demo-fork-1] skeleton.service: thinning done: rounds=3 deleted=77 skeleton_pixels=26
INFO [req=demo-fork-1] skeleton.service: graph extracted: nodes=4 edges=3
```

## 接口

- `GET /health` → 服务版本与生效配置（tile_size、max_rounds、workers）。
- `POST /skeletonize` → 完整报告：请求 ID、算法与依赖版本、逐轮删除数、分块/halo 统计、
  骨架像素、骨架图（节点 + 边像素链）、拓扑核验、耗时。
- `POST /validate` → 独立核验：`{original, skeleton}` 比较连通分量、孔洞、子集性、非空保持；
  `failure_reasons`（确定失败）与 `warnings`（不确定结论，如骨架残留 2x2 块、骨架触及边界）分列。

失败类别（HTTP 422，`error.category` 机器可读）：`NOT_BINARY`、`NOT_2D`、`TOO_SMALL`、
`DECODE_FAILED`、`MISSING_IMAGE`、`SHAPE_MISMATCH`。

## 测试设计（tests/）

- **夹具**：环（方环 + 圆环）、细桥（1px 线）、分叉（Y 形粗笔画）、跨块笔画（40x40 斜线穿多个 tile）。
- **独立参考**：`test_kernel.py` 内含一份逐像素朴素 Python 重实现，向量化内核必须在随机图上与其逐像素一致
  —— 参考答案不是由被测核心自身生成。拓扑探针基于 `scipy.ndimage`，独立于内核。
- **具体断言**：环 → 0 端点 / 1 分量 / 1 孔洞；细桥 → 0 删除、骨架恒等；分叉 → 3 端点 / 1 结点 / 3 边；
  跨块笔画 → 分块结果与整图参考逐像素相等且骨架连通；边像素链逐点落在骨架上且相邻像素 8-邻接。
- **失败类别断言**：非二值像素 / 灰度 PNG → 422 `NOT_BINARY`；被切断的骨架 → `components_equal` 进入
  `failure_reasons`。

## 配置

环境变量：`SKEL_TILE_SIZE`（默认 64）、`SKEL_MAX_ROUNDS`（默认 10000）、
`SKEL_TILE_WORKERS`（默认 1，>1 时多线程计算候选，提交仍同步）、`SKEL_LOG_LEVEL`。

## 已知边界

- Zhang-Suen 在偶数宽度笔画拐角可能残留 2x2 块（不违反拓扑契约）；图提取会把长度 ≤3 的
  结点自环毛刺吸收回结点簇，核验报告以 warning 形式提示"骨架含 2x2 块"。
- 粗笔画端头是钝的（如 3x3 刷头）时，端点位置会内缩，但端点数量保持。
