# 验收运行记录

日期：2026-10-03。环境：Linux 6.8.0-90-generic，Python 3.12.3。
依赖版本（requirements.txt）：fastapi 0.141.1、uvicorn 0.54.0、numpy 2.4.6、
scipy 1.15.3、pillow 10.2.0、pydantic 2.13.4、pytest 9.1.1、httpx 0.28.1。

## 1. 测试套件

命令：`python3 -m pytest`（干净目录，按 README 复现步骤）

结果：**34 passed**（约 3 秒）。覆盖：

- 数值内核：手工计算的 4×4→2×2、3×5→2×3（奇数边缘）、单行/单列、常数图、
  棋盘 127.5、偶数尺寸总和守恒、13×11 随机图对照独立参考、非 2D 输入拒绝。
- 层间映射：7×5→4×3→2×2→1×1 尺寸链、ceil 递推、像素中心固定映射值、
  奇数尺寸下全部源像素被覆盖（不丢行列）、非法层拒绝。
- 瓦片存储：发布/读取往返、跨 2×2 瓦片奇数偏移拼接、缺失层级 404 类、
  重复发布状态冲突、损坏瓦片 IntegrityError（非全黑）、发布中途崩溃无残留、
  未 rename 的临时目录不可见。
- 分块作业：13×11 奇数尺寸金字塔逐层对照独立参考、跨瓦片拼接、层数超限拒绝、
  日志含 run_id 与 level_published 事件。
- 服务端到端：区域数值对照参考、npy 往返、缺失层级 404 `state_conflict`、
  越界 422 `input_error`、超限 413 `resource_exhausted`、未知生成器 422、
  未知图像 404、损坏瓦片 500 `compute_failure`、错误响应携带 run_id。

## 2. 真实服务冒烟（干净数据目录 + uvicorn）

启动：

```bash
PYRAMID_DATA_ROOT=/tmp/pyramid-demo/data PYRAMID_LOG_FILE=/tmp/pyramid-demo/service.jsonl \
python3 -m uvicorn pyramid_service.service:app --port 8471
```

### 2.1 构建奇数尺寸金字塔

```bash
curl -s -X POST localhost:8471/images -H 'Content-Type: application/json' -d \
  '{"generator":"checkerboard","width":513,"height":257,"params":{"period":7},"levels":4}'
```

实际返回（摘录）：

```
run_id: run-20261003T014533-3a0afa6a
image_id: img-20261003T014533-624c97d7
levels: [(0, 513, 257), (1, 257, 129), (2, 129, 65), (3, 65, 33)]
```

层尺寸链符合 ceil 递推，奇数边界未丢行列。

### 2.2 跨瓦片区域与独立参考对比

```bash
curl -s "localhost:8471/images/img-20261003T014533-624c97d7/region?level=1&x=250&y=60&w=7&h=69&format=npy" -o region.npy
# http=200
```

与 `tests/reference.py` 的显式循环参考（整图生成 + 逐级降采样）比较：
`shape (69, 7)，max abs diff = 0.0`。x=250..256 跨越 tile_size=256 的瓦片缝，拼接精确。

### 2.3 错误分类实测

| 请求 | HTTP | category |
|---|---|---|
| `region?level=9&...`（层级未建） | 404 | `state_conflict` |
| `region?level=0&x=500&w=100&...`（越界） | 422 | `input_error` |
| 篡改瓦片文件一字节后查询命中区域 | 500 | `compute_failure`（`tile checksum mismatch ... expected=7d29ddaf... actual=39343d44...`） |

损坏瓦片返回错误而非全黑图，符合契约。

### 2.4 日志可回放

`/tmp/pyramid-demo/service.jsonl` 摘录（每行一个 JSON 事件，run_id 贯穿）：

```
run-20261003T014533-3a0afa6a level_published {'level': 0, 'width': 513, 'height': 257}
run-20261003T014533-3a0afa6a level_published {'level': 3, 'width': 65, 'height': 33}
run-20261003T014610-e545a90b request_rejected {'category': 'input_error',
  'reason': 'region (x=500, y=0, w=100, h=1) exceeds level 0 bounds 513x257'}
```

输入错误、状态冲突、资源耗尽、计算失败四类在日志与响应中均可区分。

## 3. 开发中发现并修复的问题（如实记录）

1. `synth._noise` 中 `% (1 << 64)` 对 uint64 越界抛 `OverflowError`
   —— uint64 乘法本身按 2^64 回绕，删除显式取模。
2. 两处测试用例的跨瓦片坐标越出 level 1 的 9×5 边界（测试自身错误，
   被 422 `input_error` 正确拒绝后修正坐标）。
3. 自查修复：`builder` 防御性形状检查的异常类别由 `InputError` 改为
   `ComputeError`；`service` 中函数内局部 import 上移。
