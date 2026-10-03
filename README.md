# opp495-b — 合成灰度图平移估计与对齐误差后端

两幅同尺寸 8 位灰度图的**纯平移**估计服务：FFT 相位相关 + 亚像素峰细化 +
置信度/失败分类 + 分块作业 + 对照验证。不做旋转、缩放或非刚性配准（契约第 4 条）。

## 架构

```
app/
├── config.py            # 全部阈值集中于此，可用 OPP495_* 环境变量覆盖
├── imaging.py           # 图像数据契约：base64 PNG <-> float64 灰度数组
├── schemas.py           # HTTP 边界 pydantic 模型
├── logging_setup.py     # 带 request-id 上下文变量的日志
├── kernel/
│   ├── windows.py       # 显式窗函数（hann / none）
│   ├── phasecorr.py     # 互功率谱 + 白化相关面（零幅频点显式处理）
│   ├── subpixel.py      # 对数抛物线亚像素细化 + 失败条件
│   ├── pipeline.py      # 状态机：ok / uncertain / failed + 置信度
│   └── reference.py     # 独立空域 NCC 参考（与 FFT 内核零共享代码）
├── fixtures.py          # 确定性合成夹具（真值由构造已知）
├── jobs.py              # 分块（tile）作业：逐块估计 + 置信度加权中值聚合
├── validation.py        # 验证接口：内核 vs 参考 vs 构造真值
└── main.py              # FastAPI：estimate / tiled / fixtures / validation
tests/                   # 43 个测试，断言具体数值与失败类别
fixtures/                # 生成的 PNG 夹具 + manifest.json（真值）
examples/                # 服务调用示例（python / curl）及真实输出
scripts/                 # 生成夹具 / 跑验证 / 起服务
reports/                 # validation_report.json（运行时生成）
```

## 行为契约映射

1. **零幅频点 / 窗 / 补边**（`kernel/phasecorr.py`）
   - 互功率谱幅值 `<= eps_ratio * max` 的频点**置零而非相除**，并计数为
     `degenerate_bins`；常量图 100% 退化 → `DEGENERATE_SPECTRUM` 失败。
   - 每幅图先做均值/标准差归一，再乘可配置的 Hann 窗（`window: hann|none`，
     声明式，绝不隐式）。
   - 频谱零填充 `pad_factor=2`，返回的是**线性**相关面，声明有效范围
     `|dy| <= H//2, |dx| <= W//2`，超出即出契约，不发生未声明的环绕。
   - 双幅度下限：估计面 `eps_ratio=1e-12`（保精度），歧义面
     `ambiguity_eps_ratio=1e-3`（保留晶格谱线，周期纹理的副本峰不被白化抹平）。
2. **亚像素峰估计**（`kernel/subpixel.py`）
   - 三点全正时对 log(surface) 拟合抛物线（高斯峰模型），否则退化为线性拟合。
   - 失败条件显式返回：`PEAK_ON_BORDER`、`NON_CONCAVE_NEIGHBORHOOD`、
     `FIT_OUT_OF_RANGE`；拟合失败时回退整数位移并记 `UNRELIABLE_SUBPIXEL:*`。
   - 置信度 = PSR 项 × 次峰惩罚；`status` 为 `ok / uncertain / failed`，
     失败（`failures`）与不确定结论（`uncertainties`）分列。
3. **亮度变化 vs 真无重叠**
   - 亮度变化（增益+偏移）被逐图归一吸收 → `ok`，overlap≈1。
   - 真无重叠/低重叠 → `LOW_PSR` 和/或 `INSUFFICIENT_OVERLAP`
     （overlap < 0.5 记不确定，< 0.1 记失败）；独立图像对不会报出 `ok`。
4. **仅平移**：有效范围外的位移、旋转、非刚性均不支持，属声明的限制。

## 验证材料

`fixtures/` 七组确定性夹具（`python -m app.fixtures` 重新生成）：

| 夹具 | 真值 (dy,dx) | 期望结论 |
|---|---|---|
| integer_shift | (12, -7) | ok，误差 < 0.15 px |
| subpixel_shift | (5.4, -3.65) | ok，误差 < 0.2 px |
| brightness_change | (8.25, 6.4)，B 图 ×0.65+35 | ok，误差 < 0.3 px |
| periodic_texture | (4, 4)，周期 16 棋盘格 | uncertain + `AMBIGUOUS_PEAKS`，报告歧义峰 |
| constant_image | (3, 3) | failed + `DEGENERATE_SPECTRUM` |
| low_overlap | (52, 52)，重叠 ~35% | not_ok + `INSUFFICIENT_OVERLAP` |
| independent_pair | 无（两幅独立纹理） | not_ok，且必须列出原因 |

参考答案来源独立于被测内核：真值由夹具构造可知；`kernel/reference.py`
是空域穷举 NCC（Pearson 相关），与 FFT 内核零共享代码。验证逐项检查：
状态符合期望、定位误差在容差内、参考与真值一致、内核与参考一致（仅当
内核自信时）、非 ok 结论必须列出原因。

## 复现

```bash
pip3 install -r requirements-lock.txt   # 或 requirements.txt + requirements-dev.txt

bash scripts/generate_fixtures.sh       # 生成 fixtures/*.png + manifest.json
python3 -m pytest                       # 43 个测试 + 覆盖率（当前 97%）
bash scripts/run_validation.sh          # 写 reports/validation_report.json
bash scripts/run_server.sh              # 127.0.0.1:8495
python3 examples/estimate_request.py    # 对运行中的服务发真实调用，输出存 examples/output/
```

## API

所有响应带 `request_id`（同时写入 `X-Request-ID` 响应头与每条日志）。

- `GET /health` — 版本信息（app/python/numpy/scipy/pillow）。
- `POST /v1/estimate` — `{image_a, image_b}`（base64 灰度 PNG，同尺寸）。
  返回 `status / shift / confidence / psr / overlap_fraction / peaks /
  ambiguity_peaks / failures / uncertainties / diagnostics`
  （含窗、补边、有效范围、退化比例、依赖版本、耗时）。
- `POST /v1/estimate/tiled` — 分块作业：逐块结果 + 置信度加权中值全局位移 +
  块间离散度；失败块单列不隐藏。
- `GET /v1/fixtures`、`GET /v1/fixtures/{name}` — 夹具清单与内容（含真值）。
- `POST /v1/validation/run` — 运行验证套件，返回完整报告并落盘
  `reports/validation_report.json`。

契约违规（非 base64、非 PNG、非灰度、尺寸不符、超限）→ 422 + 具体原因 +
request_id。

## 诊断与可解释性

- 每个请求有 `request_id`，日志行形如
  `... INFO opp495.api [req=671a85a26b69] estimate: status=ok shift=...`。
- `diagnostics` 展示关键处理位置：窗类型、补边因子、有效位移范围、
  退化频点比例、eps、依赖版本、耗时。
- 歧义峰（`ambiguity_peaks`）逐峰列出 (dy, dx, value)，不只给一个标志位。

## 已知限制

- 仅整图刚性平移；有效范围 ±(H/2, W/2)。
- 周期纹理只能给出"若干等价峰之一"，故报 `uncertain` 而非假装确定。
- 8 位 PNG 量化会把亚像素误差推高约 0.02–0.05 px（内存浮点输入更准）。
