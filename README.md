# opp495-a: 合成灰度图平移估计与对齐误差后端

对两幅合成灰度图做**纯平移**估计（整像素 + 亚像素），并对对齐结果做空域核验。
本后端**不做**非刚性或旋转配准——这是明确的范围边界，不是缺失的功能。

## 模块划分

| 模块 | 职责 |
|---|---|
| `app/contracts.py` | 图像与请求/响应数据契约（pydantic 校验） |
| `app/kernel/phase_correlation.py` | FFT 相位相关核：均值去除、Hann 窗、声明式补零、零幅频点显式置零 |
| `app/kernel/subpixel.py` | 亚像素峰细化（直接 DFT 求值 + 抛物线交叉校验）与一致性度量 |
| `app/kernel/verify.py` | 空域核验：重叠区 NCC、亮度增益/偏移拟合（区分亮度变化与无重叠） |
| `app/kernel/pipeline.py` | 流水线编排与状态分类，产出诊断步骤 |
| `app/jobs.py` | 分块作业：批量任务（错误隔离）与大图分块聚合 |
| `app/main.py` | FastAPI 验证接口：estimate / validate / fixtures / health / version |
| `fixtures/generate_fixtures.py` | 合成夹具生成（已知真值，独立于被测核） |
| `tests/` | 独立测试，含空域暴力参考实现 `tests/reference_impl.py` |

## 行为契约要点

1. **零幅频点处理**：互功率谱中 `|R| <= eps_rel * max|R|` 的频点被显式置零
   （默认 `eps_rel=1e-4`，高于 16 位量化噪声地板），绝不参与相位归一化除法；
   被置零频点比例以 `zero_bin_fraction` 上报。均值默认先去除（`remove_mean`），
   否则补零支撑区的 DC 泄漏会在零延迟处注入伪峰。
2. **窗函数与补边**：默认 `pad_factor=2` 声明式补零（无歧义范围扩展到 ±n），
   Hann 窗可用但默认关闭——实测它会压制位于图像边缘的重叠内容（大位移），
   并打破周期纹理别名峰的等高性从而掩盖固有歧义；权衡写在
   `app/config.py` 注释中。所有补边行为都在诊断中声明。
3. **亚像素置信度与失败条件**：置信度块含峰高、峰旁瓣比、两种独立细化
   （直接 DFT 求值 vs 抛物线）的分歧像素数、重叠区 NCC。失败类别：
   `flat_response`（常量图）、`no_common_content`（重叠区 NCC 不足）、
   `peak_at_border`（峰落在无歧义范围边缘）、`shape_mismatch`、`invalid_image`。
4. **歧义与不确定**：周期纹理在未补零相关面上做歧义扫描（补零是我们的
   产物，会污染严格周期内容），多候选峰按最小范数规则选主峰并标记
   `ambiguous_peaks`；重叠率低于阈值标记 `low_overlap`。不确定结论与失败
   原因分字段列出。
5. **亮度变化 vs 真无重叠**：重叠区零均值 NCC 对线性亮度变换不变——亮度
   变化仍通过核验并拟合出 gain/offset 上报（`brightness_change` 提示，
   不影响 ok 状态）；真正无共同内容的图像对 NCC 低，判
   `no_common_content` 失败。

## 快速开始

```bash
pip install -r requirements.txt
python fixtures/generate_fixtures.py      # 重新生成夹具（测试也会自动生成）
python -m pytest                          # 30 个测试
python -m uvicorn app.main:app --port 8000
bash examples/curl_examples.sh            # 或 python examples/estimate_request.py
```

## 接口

- `GET  /v1/health` / `GET /v1/version` — 存活与版本（app/kernel/numpy/scipy）
- `GET  /v1/fixtures` — 夹具列表与真值
- `POST /v1/estimate` — 平移估计；图像来源为 base64 PNG 或夹具引用；
  可带 `request_id` 与 `config` 覆盖项
- `POST /v1/estimate/tiled` — 大图分块聚合估计
- `POST /v1/validate` — 对照夹具真值验证，报告定位误差与类别匹配

每个响应含 `request_id`（与日志中的 `request_id=` 关联）、`versions`、
`diagnostics.steps`（关键步骤名称、耗时与处理位置，如窗/补边/置零比例）、
`confidence`、`uncertainties` 与 `failure_reason`（后两者互斥语义，分列）。

复现说明与实测结果见 [docs/REPRODUCE.md](docs/REPRODUCE.md)，
原始运行记录保存在 `results/`。
