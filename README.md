# Marker Watershed Backend

基于标记的分水岭分割后端。输入梯度（高程）图与种子标记，输出盆地标签与边界脊线。
技术栈：Python 3.12 · FastAPI · NumPy · SciPy · Pillow。所有数据均为本地合成夹具，无外部账号与真实业务数据。

## 本地验证命令

```bash
# 1. 完整测试 + 覆盖率（预期：64 passed，覆盖率 >= 80%，当前 94%）
./scripts/run_checks.sh

# 2. 仅跑测试
python3 -m pytest

# 3. 启动服务并手工验证
python3 -m uvicorn app.main:app --port 8931
curl -s localhost:8931/health
curl -s -X POST localhost:8931/v1/segment -H 'content-type: application/json' \
  -d '{"elevation": [[1,3,5,4,2]], "markers": [[1,0,0,0,2]], "connectivity": 4}'
# 预期 labels: [[1,1,1,-1,2]]，boundary 第 4 列为 true（-1 = 脊线）
```

预期判断方式：pytest 全绿且覆盖率达标；`/v1/segment` 对上述手工算例返回
`[[1,1,1,-1,2]]`；`/health` 返回全部依赖版本；作业接口
`POST /v1/jobs` → `GET /v1/jobs/{id}` 状态由 `queued/running` 收敛到
`succeeded` 或 `failed`（失败必带 `error.category`，绝不以成功返回）。

## 模块关系

```
app/
  config.py          配置层：环境变量 -> Settings；依赖版本收集
  logging_setup.py   JSON 结构化日志，每条记录携带 run_id / input_sha256
  contracts.py       图像数据契约：pydantic 模式 + 语义校验（问题列表）
  core/
    connectivity.py  固定邻域表（4-/8-连通唯一定义）
    gradient.py      Sobel 梯度幅值（可选高斯预平滑）
    watershed.py     数值内核：确定性优先洪水淹没
  jobs/manager.py    分块作业：后台线程按 chunk 出堆、逐块上报进度
  api/routes.py      验证接口 /v1/validate、同步分割 /v1/segment、
                     PNG 分割 /v1/segment/image、分块作业 /v1/jobs*
  main.py            应用工厂，全局异常 -> 分类错误（绝不伪装成功）
tests/
  fixtures.py        合成夹具 + 手工计算的期望数组（非内核生成）
  reference_impl.py  独立参考实现（桶式水位推进，仅测试用）
  test_*.py          内核 / 梯度 / API / 作业测试
```

调用方向：`api → contracts / jobs → core`，`core` 不依赖上层。

## 算法假设（判定依据）

1. **淹没模型**：Meyer 式优先洪水。全局最小堆键为 `(elevation, flat_index)`，
   按水位从低到高扩张；输出是水位传播结果（盆地 + 脊线），**不是**最近种子
   距离分配——`tests/fixtures.py` 的 corridor 夹具中，像素 (1,2) 欧氏距离
   离种子 1 更近，但经低高程走廊被盆地 2 淹没，测试对此有专门断言。
2. **平台次序确定性**：相同高程平台的处理顺序由行主序扁平索引唯一决定
   （堆键第二分量），与队列偶然性、线程调度、运行次数无关。测试：同一输入
   重复运行逐位一致；`chunk_size` 任意取值结果一致；标记标签整体置换只
   置换输出标签。
3. **连通性固定**：仅支持 4-连通（von Neumann）与 8-连通（Moore），偏移表
   是唯一定义来源，其他取值报 `bad_connectivity`。
4. **脊线定义**：像素被弹出时若其邻域已带不同盆地标签，则被重分类为
   脊线像素（标签 `-1`），并计入 `conflict_count`。由此保证：任意两个
   不同盆地的非种子像素不会直接相邻（有不变量测试）。
5. **冲突种子**：种子永不被降级。相邻异标签种子各自保留标签，冲突计入
   `seed_conflict_count`，不产生脊线像素。
6. **无种子区**：可选 `mask` 之外的像素保持 `0`（UNLABELED）并计入
   `pixels_unlabeled`；标记全空报 `no_seeds`；种子落在 mask 外报
   `seed_outside_mask`。
7. **平局代价**：平局列/平台的分界线落在高索引一侧（低索引侧先淹没），
   这是规则 2 的直接推论，two_basins 与 saddle 夹具的手工期望即按此推得。

## 测试与参考答案

- 小夹具（1D 双盆地、1D/2D 平台、走廊、鞍点、双盆地场、冲突种子、mask）
  的期望数组全部为**手工推算的字面量**，非被测内核生成。
- 大夹具（16×16 含噪梯度，固定种子 20261002）与**独立参考实现**
  （`tests/reference_impl.py`，桶式水位推进，与内核不共享代码）交叉校验，
  并断言种子保留、各盆地单连通分量、排列/分块稳定性。
- 失败类别测试：`no_seeds`、`shape_mismatch`、`non_finite_elevation`、
  `negative_marker`、`bad_connectivity`、`seed_outside_mask`、
  `payload_too_large`、`unsupported_image_mode`、`job_not_found` 等均断言
  具体类别，而非仅检查“接口能调用”。
- 日志测试：`run_started/run_finished/job_chunk_done` 事件携带
  `run_id`、`input_sha256`、依赖版本与 `tie_break_rule` 判定依据。

## 依赖版本（本机实测）

| 依赖 | 版本 |
|---|---|
| Python | 3.12.3 |
| fastapi | 0.141.1 |
| numpy | 2.4.6 |
| scipy | 1.15.3 |
| pillow | 10.2.0 |
| pydantic | 2.13.5 |
| uvicorn | 0.54.0 |
| pytest / pytest-cov / httpx（测试） | 9.1.1 / 7.1.0 / 0.28.1 |

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `WATERSHED_MAX_IMAGE_PIXELS` | 4,000,000 | 单请求像素上限，超出返回 413 |
| `WATERSHED_DEFAULT_CHUNK_SIZE` | 4096 | 分块作业每批出堆像素数 |
| `WATERSHED_MAX_RETAINED_JOBS` | 128 | 内存中保留的作业记录数 |
| `WATERSHED_LOG_LEVEL` | INFO | 日志级别 |

## 已知边界

- 单进程内存作业存储，重启即失；水平扩展需外置队列（未实现，YAGNI）。
- 分块仅影响进度上报粒度，不改变数值结果（有专门测试保证）。
