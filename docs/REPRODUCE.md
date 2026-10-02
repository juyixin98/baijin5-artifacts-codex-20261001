# 复现文档

## 环境

- Python 3.12，依赖锁定于 `requirements.txt`（numpy 2.4.6 / scipy 1.15.3 /
  pillow 10.2.0 / fastapi 0.141.1 / uvicorn 0.54.0 / pytest 9.1.1 / httpx 0.28.1）。
- 全部数据为本地合成夹具，无外部服务、无生产账号。

## 复现步骤

```bash
pip install -r requirements.txt
python fixtures/generate_fixtures.py   # 生成 fixtures/data/*.png + manifest.json
python -m pytest                       # 单元/分类/接口测试（自动重建夹具）
python -m uvicorn app.main:app --port 8000
bash examples/curl_examples.sh         # 服务调用示例（正常 + 异常路径）
python examples/estimate_request.py    # Python 客户端示例
```

## 夹具与预期类别（真值来自生成参数，非被测核输出）

| 夹具 | 真值位移 (dy, dx) | 预期类别 | 说明 |
|---|---|---|---|
| `integer_shift` | (5, -3) | ok | 整像素位移 |
| `subpixel_shift` | (2.4, -1.7) | ok | 亚像素位移（样条重采样生成） |
| `brightness_change` | (3.25, 4.5)，增益 1.25 偏移 18 | ok + brightness_change | 位移叠加线性亮度变化 |
| `periodic_texture` | (4, 2)，周期 16 px | uncertain / ambiguous_peaks | 纯周期纹理，位移仅模周期有定义 |
| `constant_image` | — | failed / flat_response | 常量图，无相位信息 |
| `low_overlap` | (90, 0) | uncertain / low_overlap | 重叠率 ≈0.30 |
| `no_overlap` | — | failed / no_common_content | 两幅独立纹理 |

位移生成用 `scipy.ndimage.shift`（空域样条），与频域被测核实现路径不同；
测试另有 `tests/reference_impl.py` 纯空域暴力 NCC 参考（无 FFT）交叉验证整数位移。

## 实测结果（2026-10-03，本仓库 `results/` 留有原始记录）

`python -m pytest`：**30 passed**（`results/pytest_run.txt`）。

`POST /v1/validate`（tolerance 0.5 px，7/7 类别匹配，`results/validate_all.json`）：

| 夹具 | 观测状态 | 定位误差 (px) |
|---|---|---|
| integer_shift | ok | 0.028 |
| subpixel_shift | ok | 0.067 |
| brightness_change | ok (+brightness_change) | 0.071 |
| periodic_texture | uncertain (ambiguous_peaks) | 0.0（最小范数别名=真值） |
| constant_image | failed (flat_response) | — |
| low_overlap | uncertain (low_overlap) | 0.221 |
| no_overlap | failed (no_common_content) | — |

异常路径实测：畸形 base64 负载 → HTTP 400；未知夹具 → 404；
形状不匹配 → 200 + `status=failed, failure_reason=shape_mismatch`
（`results/smoke_*.json`）。

## 已知边界（声明，非缺陷）

- 仅纯平移；旋转/非刚性配准明确不支持。
- 无歧义位移范围由 `pad_factor` 决定（默认 ±n），更大位移会按 DFT 取模
  混叠并在诊断中体现。
- 周期内容的位移只定义到模周期；服务返回候选峰列表与最小范数主峰。
- 8 位量化输入的量化噪声地板接近默认 `eps_rel=1e-4` 的量级，必要时用
  请求级 `config.eps_rel` 调整（契约允许每次请求覆盖）。
