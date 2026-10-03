# geodesic-recon

灰度/二值图像在掩膜约束下的测地膨胀重建服务（morphological reconstruction by dilation）。

给定标记 `marker ≤ mask`，迭代

```
rec(0)   = marker
rec(n+1) = min(dilate(rec(n)), mask)
```

至不动点。结果即标记在掩膜下的测地重建，满足幂等、单调（关于标记）、且不超过掩膜。

## 目录结构

```
config/default.toml        # 部署配置（可用 GEOREC_<KEY> 环境变量覆盖）
src/geodesic_recon/
  config.py                # 配置加载与校验
  errors.py                # 错误分类（稳定 category 字符串）
  contracts.py             # 图像数据契约：强制 marker<=mask，拒绝或显式裁剪
  kernel.py                # 数值内核：同步迭代（参考语义）+ FIFO 队列传播
  tiles.py                 # 分块作业：带 1px 只读 halo 的分块扫描至全局不动点
  validation.py            # 验证接口：不动点/幂等/单调/不超掩膜（用 SciPy 独立复核）
  diagnostics.py           # 请求级日志：request_id + 仅聚合统计的脱敏图像信息
  service.py               # FastAPI 接口
tests/                     # 独立组织的测试（含手工计算夹具与朴素参考实现）
examples/reconstruct_request.json
docs/ACCEPTANCE.md         # 验收执行记录
```

## 关键规则（固定，不可配置）

- **邻域**：4 或 8 连通（部署级配置 `connectivity`，默认 4）。
- **边界规则**：`edge-ignore` —— 越界邻居直接忽略，不回绕、不复制边缘。
- **标记约束**：`marker > mask` 时按 `on_violation` 拒绝（`MARKER_EXCEEDS_MASK`，HTTP 422）或显式裁剪（响应 `clipped=true`）。

## 算法等价性

三个内核结果一致（测试对随机输入与全部夹具断言逐项相等）：

- `sync`：整图同步迭代，参考语义，记录每轮变化像素数；
- `queue`：FIFO 队列传播（Vincent 风格），只访问仍可增长的像素，记录入/出队次数；
- `tiled`：分块扫描，记录扫描轮数与每轮变化数。

## 安装与运行

```bash
pip install -r requirements.txt   # 版本已固定，见文件
PYTHONPATH=src python3 -m uvicorn geodesic_recon.service:app --port 8474
```

## 测试

```bash
python3 -m pytest        # 137 个测试
```

测试包含：细桥、孔洞、平坦区、边界标记（含手工计算的期望数组，非由被测内核生成）、
与测试侧独立朴素参考实现（纯 Python 逐像素循环）的逐项对照、收敛步数记录断言、
各失败类别（`MARKER_EXCEEDS_MASK` / `SHAPE_MISMATCH` / `NON_FINITE` / `BAD_RANK` 等）、
幂等/单调/不超掩膜性质、分块等价性、HTTP 接口与 PNG 往返。

## API

### `POST /v1/reconstruct`

```bash
curl -X POST http://127.0.0.1:8474/v1/reconstruct \
  -H 'Content-Type: application/json' -H 'X-Request-ID: demo-1' \
  --data @examples/reconstruct_request.json
```

请求体：`{marker, mask, connectivity?, on_violation?, algorithm?}`（`marker`/`mask` 为等形二维数组）。
响应：`{request_id, clipped, result, trace, validation}`；拒绝时 HTTP 422 +
`{request_id, error: {category, detail}}`。

### `POST /v1/validate`

校验一个声称的结果是否满足 不动点 / ≥marker / ≤mask，并附幂等性检查。

### `POST /v1/reconstruct/image`

multipart 上传 `marker`、`mask` 两个 PNG（灰度），返回 16 位灰度 PNG 结果。

### `GET /health`

返回生效配置。

## 诊断与脱敏

每个请求分配 `X-Request-ID`（可自带，响应回显）。日志只记录聚合统计
（shape/dtype/min/max/mean）与接受/拒绝原因，绝不输出原始像素数据。
