# skeleton-backend

二值图拓扑保持细化（Zhang-Suen 并行细化）与骨架图提取后端。
技术栈：Python 3.12、FastAPI、NumPy、SciPy、Pillow。所有输入均为本地合成夹具，无外部账号与真实业务数据。

## 模块划分

| 模块 | 职责 |
|---|---|
| `app/contracts.py` | 图像数据契约：二值、矩形、尺寸上限；错误分类 `contract_violation` / `unknown_sample` / `engine_error` |
| `app/kernel.py` | 数值内核：Zhang-Suen 并行细化，纯函数，报告每轮两个子迭代的删点数 |
| `app/tiling.py` | 分块作业：halo 交换 + 屏障同步，逐块计算、全局同步应用 |
| `app/graph.py` | 骨架图提取：端点/交叉点聚类为节点，边保存原始像素链 |
| `app/validation.py` | 验证接口：连通分量、孔洞、端点约束的前后对照，失败与不确定结论分列 |
| `app/main.py` | FastAPI 接口层：请求身份关联、结构化日志、错误分类 |
| `app/samples.py` | 本地合成夹具（环、细桥、分叉、跨块笔画等），`data/*.png` 的唯一来源 |

## 行为契约

1. **相容邻接**：前景 8-连通、背景 4-连通（`app/validation.py` 中 `FG_STRUCTURE` / `BG_STRUCTURE`）。
2. **同一前态删除**：每个子迭代内，删除掩码只由同一前态计算并同时应用；
   子迭代 2 读取子迭代 1 的结果（Zhang-Suen 标准定义）。测试
   `test_deletion_mask_uses_single_prior_state` 用 2x2 块验证：4 个像素在同一子迭代被同时标记。
3. **端点与孔洞保留**：端点（B=1）永不被删；A(P)=1 条件阻止孔洞与外背景合并。
   环样例验证：分量 1→1、孔洞 1→1、端点 0→0。
4. **分块边界交换**：每子迭代所有块从同一全局前态读取 1 像素 halo，全部算完后屏障式同步应用；
   收敛判定为全局一轮零删除。分块结果与整图参考**逐位一致**（`tests/test_tiling.py`，含跨块斜线与种子噪声）。
5. **边保存原像素链**：`GraphEdge.pixels` 为完整有序像素链（含端节点像素），
   测试断言链覆盖全部骨架像素且无重复。

## 快速开始

```bash
pip install -r requirements.txt
python -m pytest                 # 42 passed
PYTHONPATH=. python scripts/make_samples.py   # 重新生成 data/*.png
python -m uvicorn app.main:app --port 8000
```

### 调用示例（实测输出）

```bash
curl -s -X POST localhost:8000/v1/validate -H 'content-type: application/json' \
     -H 'x-request-id: demo-ring-1' \
     -d '{"sample":"ring","engine":"tiled","tile_size":8}'
```

```json
{
  "request_id": "demo-ring-1", "version": "0.1.0", "engine": "tiled",
  "rounds": 4, "converged": true, "tile_count": 16, "halo_width": 1,
  "deletions_per_round": [[70,70],[68,44],[20,0],[0,0]],
  "report": {"components_before": 1, "components_after": 1,
             "holes_before": 1, "holes_after": 1,
             "endpoints_before": 0, "endpoints_after": 0,
             "failures": [], "uncertain": [], "passed": true}
}
```

分叉样例的图映射（`POST /v1/graph {"sample":"fork"}`）：
`{"node_count": 4, "endpoint_count": 3, "junction_count": 1, "edge_count": 3, "cycle_count": 0}`，
每条边携带原始像素链（茎 9 像素、两臂各 7 像素）。

## 接口

| 方法/路径 | 说明 |
|---|---|
| `GET /health` | 存活与版本 |
| `GET /v1/samples` | 可用样例名 |
| `POST /v1/thin` | 细化；`engine=full|tiled`，返回骨架、删点轮次、分块信息 |
| `POST /v1/graph` | 细化 + 骨架图（节点、边、像素链、汇总） |
| `POST /v1/validate` | 细化 + 拓扑验证报告（failures 与 uncertain 分列） |

请求体二选一：`{"pixels": [[0,1,...], ...]}` 或 `{"sample": "ring"}`。
可选：`engine`、`tile_size`、`max_rounds`。
请求身份：响应头与响应体均回显 `x-request-id`（未提供则自动生成），日志行含 `[req=<id>]`。

错误分类（HTTP 422/500，均带 `request_id`）：
`contract_violation`（非二值、行不齐、超尺寸、输入源歧义）、
`unknown_sample`、`engine_error`。

## 配置（环境变量）

`SKELETON_TILE_SIZE`（默认 64）、`SKELETON_MAX_ROUNDS`（默认 256）、
`SKELETON_MAX_IMAGE_DIM`（默认 2048）、`SKELETON_VERSION`。

## 测试与验收

```bash
python -m pytest -q
# 42 passed
```

- 参考值手工推导，不由被测内核生成：3x3 实心块经 `(6,2)` 删点轮收敛到中心单像素；
  3x3 十字经 `(4,0)` 收敛到中心；2x4 横条剩 `{(0,1),(0,2)}` 双像素残端；1x5 直线为不动点。
- 环/细桥/分叉/跨块笔画对照整图参考：分量、孔洞、端点约束逐项断言。
- 失败类别断言：人为切断骨架 → `component_count_changed:1->2`；打开环 →
  `hole_count_changed:1->0`；前景被清空 → `foreground_erased`；空输入 →
  `empty_foreground` 列入 uncertain 而非 failure。
- 分块一致性：4 个样例 + 种子噪声图，分块与整图骨架、删点轮次逐项相等。

## 日志可解释性

每个请求一条链路：`[req=demo-ring-1] api: thinning done engine=tiled shape=(31, 31) rounds=4 deleted=272 converged=True`；
验证失败与不确定结论分别用 ERROR/WARNING 单列。
