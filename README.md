# iccconv — 基于 littleCMS 的图像颜色转换后端

处理 RGB、灰度（GRAY）和受限 CMYK 的 ICC 颜色转换服务。引擎为
littleCMS 2（经 Pillow `ImageCms` 绑定），接口为 FastAPI。

## 模块关系

```
src/iccconv/
├── contract/          图像数据契约：ImageDocument（uint8, H×W×C）、
│                      ColorSpace / AlphaMode / RenderingIntent 枚举。
│                      构造即校验，下游不再重复检查。
├── profiles/          ICC profile 验证（类别/色空间/PCS/摘要）与
│                      命名 profile 注册表（本地目录，防路径穿越）。
├── kernel/            数值内核：
│                      transform.py — 唯一接触引擎的层（建/用 transform）
│                      alpha.py     — alpha 分离/合并、预乘换算
│                      gamut.py     — 超色域往返启发式估计
│                      engine.py    — 编排：验 profile→拆 alpha→转换→
│                                      色域估计→回接 alpha→元数据
├── jobs/              分块作业：切片、逐块转换、重组、带摘要的作业记录。
├── api/               验证接口：FastAPI 应用、pydantic schema、
│                      PNG/TIFF 编解码、带请求标识的诊断日志（脱敏）。
└── config.py          环境变量配置（profile 目录、像素上限、默认块大小）。

tests/                 独立组织的测试（契约/profile/内核/alpha/色域/
                       分块/API/往返误差/独立 NumPy 参考）。
tests/golden/          引擎黄金值（由 scripts/generate_golden.py 直接驱动
                       littleCMS 生成，不经过被测内核）。
tests/reference/       独立参考实现（NumPy/SciPy 矩阵-TRC 数学，不用引擎）。
scripts/               夹具与黄金值生成脚本。
profiles/              本地 ICC profile 依赖（来源见 profiles/SOURCES.md）。
```

数据流：`API → decode → ImageDocument → engine.convert_document →
kernel.transform（littleCMS）→ ImageDocument → encode → 响应`。

## 关键语义与假设

- **不猜 profile**：图像无内嵌 profile 且未显式指定源 profile 时，请求被
  拒绝（`missing_profile`），绝不默认 sRGB。源 profile 三种给法：注册表
  `name`、内联 `icc_b64`、`embedded: true`（用图像内嵌的）。
- **Profile 先验证**：转换前验证两端 profile——必须可解析、类别为
  mntr/scnr/prtr（拒绝 link/abst/spac/nmcl）、色空间 ∈ {RGB, GRAY, CMYK}、
  PCS ∈ {XYZ, Lab}；源 profile 色空间必须与图像色空间一致，否则
  `profile_role_mismatch`。
- **Alpha 与颜色分离**：alpha 从不进 ICC 变换，逐位保留。转换恒在
  非预乘（straight）颜色上进行；预乘输入先除 alpha（alpha=0 定义为颜色 0），
  输出按 `alpha_mode_out` 决定是否重新预乘。
- **受限 CMYK**：CMYK 图像不允许携带 alpha（引擎绑定无 CMYKA 模式），
  契约层直接拒绝（`unsupported_alpha`）。
- **渲染意图与黑点补偿**：四种 ICC intent 均可选，引擎报告不支持时拒绝
  （`intent_unsupported`）；intent 与 BPC 原样写入结果元数据。
- **不宣称无损**：跨色域转换永远 `lossless: false` 并附说明；超色域检测为
  往返启发式（`certainty: heuristic`），不是证明。
- **位深**：仅 uint8；16 位/浮点管线明确不在范围内。
- **分块一致性**：引擎变换是逐像素的，分块结果与整图转换逐位一致
  （有测试断言）。

## 依赖版本（本机实测）

| 依赖 | 版本 |
| --- | --- |
| Python | 3.12.3 |
| Pillow | 10.2.0（littleCMS 2.14） |
| NumPy | 2.4.6 |
| SciPy | 1.15.3（独立参考实现用 `scipy.linalg`） |
| FastAPI | 0.141.1 / pydantic 2.x |
| pytest | 9.1.1（+ pytest-cov 7.1.0、httpx 0.28.1、uvicorn 0.54.0） |

ICC profile 为本机 `colord`/`ghostscript` 包中文件的未修改副本
（`profiles/SOURCES.md` 有清单）；坏 profile 夹具由脚本确定性生成。
无网络、无生产账号、无真实业务数据。

## 本地验证命令

```bash
# 0. 环境（PEP 668 系统 Python 需用 venv）
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e . --no-build-isolation

# 1. 重建本地夹具（复制系统 profile、生成坏 profile）与黄金值
.venv/bin/python scripts/generate_fixtures.py
.venv/bin/python scripts/generate_golden.py

# 2. 全部测试 —— 预期：58 passed
.venv/bin/python -m pytest

# 3. 覆盖率 —— 预期：TOTAL ≥ 80%（实测 93%）
.venv/bin/python -m pytest --cov=iccconv --cov-report=term-missing

# 4. 启动服务并冒烟
.venv/bin/python -c "from iccconv.api.app import create_app; import uvicorn; \
  uvicorn.run(create_app(), host='127.0.0.1', port=8931)"
curl -s localhost:8931/v1/health      # → {"status":"ok","engine":"littleCMS 2.14"}
curl -s localhost:8931/v1/profiles    # → 注册表中的 profile 名列表
```

转换调用示例：

```bash
python3 - <<'EOF'
import base64, io, json, urllib.request
import numpy as np
from PIL import Image
img = np.zeros((8, 8, 3), np.uint8); img[..., 0] = 255   # 纯 sRGB 红
buf = io.BytesIO(); Image.fromarray(img).save(buf, "PNG")
req = urllib.request.Request(
    "http://127.0.0.1:8931/v1/convert",
    data=json.dumps({
        "image_b64": base64.b64encode(buf.getvalue()).decode(),
        "source": {"name": "sRGB.icc"},
        "target": {"name": "SWOP_TR003_coated_3.icc"},
        "intent": 1, "black_point_compensation": True, "check_gamut": True,
    }).encode(),
    headers={"Content-Type": "application/json"},
)
print(json.dumps(json.load(urllib.request.urlopen(req)), indent=1)[:800])
EOF
# 预期：status=accepted，output_format=tiff，gamut.flagged_pixels=64，
#       metadata 记录 intent/BPC/两端 profile 摘要，lossless=false。
```

## 证据组织（测试如何断言）

- **黄金值对照**（`tests/test_kernel.py`）：12 组用例（sRGB↔AdobeRGB、
  sRGB↔SWOP CMYK、sRGB↔sgray、恒等、通道交换），期望值由
  `scripts/generate_golden.py` 直接驱动引擎生成并落盘为 JSON，内核结果
  必须逐字节相等；引擎版本变化时该测试跳过并提示重新生成。
- **独立参考**（`tests/test_reference_numpy.py`）：不经过引擎的
  NumPy/SciPy 矩阵-TRC 实现（sRGB EOTF、Bradford D65→D50、AdobeRGB
  γ=563/256），与内核 sRGB→AdobeRGB 结果差异 ≤ 1 LSB。
- **通道顺序**：`SwappedRedAndGreen.icc` 证明红绿通道不混淆。
- **透明边缘**：alpha 渐变边缘经转换后逐位不变；预乘/非预乘两条路径
  结果一致（容差内有解析界）。
- **超色域**：纯 sRGB 红/绿/蓝转 SWOP CMYK 被往返启发式标记；中性灰不标记。
- **坏 profile**：截断、随机字节、named-color 类别、空字节分别断言
  `invalid_profile`；缺 profile 断言 `missing_profile`；色空间不符断言
  `profile_role_mismatch`。
- **往返误差说明**：sRGB→AdobeRGB→sRGB 色域内颜色误差 ≤ 2 LSB；
  sRGB→CMYK→sRGB 饱和原色误差 > 10 LSB（证明不宣称无损）。
- **诊断与脱敏**：每个响应带 `request_id` 与分步诊断（accepted /
  rejected / undetermined 及原因）；日志只含 sha256 前 16 位、尺寸、
  枚举名，测试断言日志中不出现图像/profile 的 base64 原文。

## 测试状态（如实记录）

- 已运行：58 个测试全部通过（2026-10-04，上述环境）；覆盖率 93%。
- 跳过条件：`test_golden_cases_match_kernel` 在 lcms 版本与黄金值生成
  版本不一致时跳过（需重跑 `scripts/generate_golden.py`）。
- 未覆盖路径：`intent_unsupported` 在本机 profile 上不可触发（lcms 2.14
  对所有四个 intent 均报告支持），仅以 monkeypatch 单元测试覆盖该分支；
  引擎故障（`engine_failure`）分支同理仅以构造性输入间接覆盖。

## 已知限制

- 仅 uint8；CMYK 无 alpha；超色域为启发式估计；黄金值与引擎版本绑定；
  16 位、Lab/XYZ 直接输入、设备链接 profile 均不在范围内。
