# 验收记录（ACCEPTANCE）

记录时间：2026-10-03。环境：Python 3.12.3，Linux 6.8，依赖版本见 `requirements.txt`
（numpy 2.4.6 / scipy 1.15.3 / pillow 10.2.0 / fastapi 0.141.1 / uvicorn 0.54.0 /
pytest 9.1.1 / httpx 0.28.1）。

## 1. 测试套件

命令：`python3 -m pytest -q`（从干净目录，无需预先构建）

结果：**55 passed, 1 warning in ~1.2s**（warning 来自 starlette TestClient 的
httpx 弃用提示，与本工程无关）。另从干净复制的目录（无缓存、无存储残留）重跑
`python3 -m pytest -q`，结果相同。

覆盖要点与对应测试文件：

| 验收点 | 测试 |
|---|---|
| 层间像素中心映射固定、奇数尺寸不丢行列 | `test_coords.py`（7×5→4×3→2×2→1×1，字面值钉死映射） |
| 面积/高斯内核正确性 | `test_kernel.py`（手算 3×5 字面值、朴素循环参考、Pillow BOX 第三方参考） |
| 跨瓦片精确拼接 | `test_region.py`（区域 vs 整层参考，area 与 gaussian 逐像素相等） |
| 层级元数据+内容原子发布 | `test_store.py`（重复发布 409、发布中途崩溃不留层级） |
| 损坏瓦片不以全黑冒充 | `test_store.py` / `test_api.py`（sha256 校验失败 → 500 compute_failure，validate 报告坏瓦片） |
| 层缺失 | `test_region.py` / `test_api.py`（404 not_found） |
| 错误分类可区分 | `test_api.py::test_error_categories_are_distinguishable`（400/404/409/507） |
| 路径穿越防护 | `test_store.py::test_pyramid_id_cannot_escape_store_root`（非法 id → 400，不拼路径） |
| 构建失败回滚 | `test_builder.py::test_failed_build_rolls_back_and_id_can_be_retried`（中途崩溃不留半成品层级，id 可重试） |
| 不完整层级拒发 | `test_store.py::test_incomplete_tile_iterator_refuses_to_publish` |
| 奇数尺寸高斯内核 | `test_builder.py::test_gaussian_kernel_on_odd_sizes`（21×13 逐层对照） |
| 3 通道端到端 | `test_builder.py::test_three_channel_end_to_end` |
| 2×2 瓦片角拼接 | `test_region.py::test_region_straddling_four_tiles` |
| 畸形源文件 | `test_builder.py::test_malformed_npy_source_is_an_input_error` + API 侧 400 |
| run_id 与判断理由落日志 | `test_builder.py::test_build_logs_run_id_and_decisions` |

参考答案独立性：手算字面值 + 测试内朴素循环重实现 + Pillow BOX（第三方面积重采样）
三方互证，并非全部由被测内核生成。

## 2. 端到端复现（干净存储目录 + 真实 HTTP 服务）

启动：`PYRAMID_STORE_DIR=/tmp/accept_store uvicorn pyramid_service.api:app --port 8471`

实际执行与返回（原样记录）：

- `GET /health` → `{"status":"ok"}`
- `POST /pyramids`（65×33 棋盘，tile 16，4 层，area）→ 201，
  `run_id=785e410b4dc5`，层级 `(65,33) (33,17) (17,9) (9,5)`，瓦片数 15/6/2/1。
- `GET /pyramids/demo/levels` → 各层元数据与 `run_id` 一致。
- `GET .../region?level=1&x=14&y=3&w=8&h=6&format=npy`（跨瓦片）→ 200，
  `(6,8,1) float64`，全 0.5（1px 棋盘面积平均的正确结果）。
- `POST /pyramids`（20×14 渐变，tile 8，gaussian）→ 201；`region?level=1&x=6&y=2&w=4&h=4`
  与直接全图内核参考的最大绝对误差 **0.0**（跨瓦片高斯拼接与整层计算逐比特一致）。
- 错误分类实测：
  - `level=99` → 404 `not_found`
  - `w=0` → 400 `input_error`
  - 重复构建同 id → 409 `state_conflict`
- 损坏注入实测：将 `levels/1/tiles/0_0.npy` 覆写为 `GARBAGE` 后 —
  - 区域请求 → 500 `compute_failure`，消息含期望/实际 sha256（**未**返回全黑图）；
  - `GET .../validate?level=1` → `ok:false`，`bad` 列出 `(tx=0,ty=0)` 及原因；
  - 健康金字塔 `grad` → `ok:true`（3 层共 9 瓦片全部通过）。
- 服务日志（JSON 行，含 run_id 与判断理由）：`build_start`（`reason:"explicit level
  count"`）、`level_published`×7、`region_request`/`region_read`、`build_done`。

## 3. 独立审查与修复

完成初版后进行了一次独立对抗性代码审查（子代理通读全部源码与测试），无 CRITICAL；
据此修复并补测：畸形 `.npy` 源的错误分类泄漏（HIGH）、不完整层级误发布、失败构建
不可重试（现回滚并记录 `build_rolled_back`）、`levels` 无上限、像素上限未计通道数、
暂存目录残留、清单缓存掩盖篡改（validate 现绕过缓存）、清单内非法瓦片路径、
`source.json` 损坏未分类、422/未捕获异常未走统一错误信封、`format` 校验顺序等。

## 4. 已知边界（如实说明）

- 资源上限为进程内配置（`PYRAMID_MAX_SOURCE_PIXELS` / `PYRAMID_MAX_REGION_PIXELS`），
  按 W×H×C 计值，超限返回 507；无磁盘配额管理。
- `npy` 源路径由请求方提供：本服务定位为本地可信工具，未做路径白名单；暴露到
  不可信网络前应加路径约束或鉴权。
- 高斯内核为"预滤波 + 固定中心采样"，与面积内核共享同一中心映射；两者数值结果
  不同属预期（测试分别锚定）。
- 层级在内存中逐瓦片计算、经瓦片存储回读上一层；单层瓦片读取窗口有界
  （`(2·tile_size + 2·margin)²`），源图可大于内存（`.npy` 内存映射）。
