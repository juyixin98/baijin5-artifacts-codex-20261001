# 验收记录（2026-10-03 实跑结果）

环境：Linux 6.8.0-90-generic，Python 3.12.3，pip 24.0。

## 1. 依赖（requirements.txt 已固定版本，实机核对一致）

```
numpy==2.4.6  scipy==1.15.3  Pillow==10.2.0  fastapi==0.141.1
uvicorn==0.54.0  pydantic==2.13.5  pytest==9.1.1  httpx==0.28.1
python-multipart==0.0.32
```

## 2. 测试

命令：`python3 -m pytest`
结果：**137 passed, 1 warning in 1.40s**（warning 为 starlette TestClient 的弃用提示，与功能无关）。

覆盖要点：

- 细桥 / 孔洞 / 平坦区 / 边界标记（防回绕）/ 二值掩膜 六组夹具，期望值为手工计算的字面量数组；
- sync / queue / tiled 三内核与测试侧独立朴素参考（纯 Python 逐像素循环）逐项相等；
- 收敛步数被记录并断言（sync 迭代轮数与朴素参考一致；tiled 末轮变化数为 0）；
- 失败类别逐项断言：MARKER_EXCEEDS_MASK、SHAPE_MISMATCH、NON_FINITE、BAD_RANK、
  DTYPE_UNSUPPORTED、EMPTY_IMAGE、IMAGE_TOO_LARGE、MALFORMED_IMAGE、BAD_POLICY 等；
- 性质：幂等、单调、不超掩膜、≥标记；验证接口对篡改结果能定位到具体失败检查项。

## 3. 服务实跑

启动：`PYTHONPATH=src python3 -m uvicorn geodesic_recon.service:app --port 8474`

- `GET /health` → `{"status":"ok","config":{"connectivity":4,"boundary_rule":"edge-ignore",...}}`
- `POST /v1/reconstruct`（examples/reconstruct_request.json，细桥 5×7）→ 200，
  结果与手工期望逐项一致（墙体列保持 0，桥孔及其余区域=100），
  `trace={algorithm:queue, initial_queue_size:1, pushes:31, pops:31}`，`validation.ok=true`，
  请求头 `X-Request-ID: demo-accept-1` 在响应头与响应体中回显。
- `POST /v1/reconstruct`（marker=[[5]], mask=[[3]]）→ 422
  `{"error":{"category":"MARKER_EXCEEDS_MASK","detail":"marker exceeds mask at 1 pixel(s), worst excess 2"}}`
- 同上加 `"on_violation":"clip"` → 200，`clipped=true`，`result=[[3.0,2.0]]`。
- `POST /v1/reconstruct/image`（flat_zone 4×4 PNG 上传）→ 200 `image/png`，
  解码结果全为 60，与期望一致；响应头含 `x-request-id`、`x-clipped: false`。

## 4. 诊断日志（脱敏核验）

实跑日志样例（像素数据不出现，仅聚合统计 + request_id）：

```
INFO [demo-accept-1] geodesic_recon.service: request.accepted marker={'shape': [5, 7], 'dtype': 'float64', 'min': 0.0, 'max': 100.0, 'mean': 2.857143} mask={...} clipped=False algorithm=queue connectivity=4
INFO [demo-accept-1] geodesic_recon.service: request.completed trace={'algorithm': 'queue', 'initial_queue_size': 1, 'pushes': 31, 'pops': 31} validation_ok=True
INFO [1ba395c3...] geodesic_recon.service: request.rejected category=MARKER_EXCEEDS_MASK detail=marker exceeds mask at 1 pixel(s), worst excess 2
```

## 5. 从干净目录复现步骤

```bash
pip install -r requirements.txt
python3 -m pytest                                        # 期望：137 passed
PYTHONPATH=src python3 -m uvicorn geodesic_recon.service:app --port 8474
curl -X POST http://127.0.0.1:8474/v1/reconstruct \
  -H 'Content-Type: application/json' --data @examples/reconstruct_request.json
```
