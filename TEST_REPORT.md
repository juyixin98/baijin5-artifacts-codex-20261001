# 测试执行报告

日期：2026-10-04　环境：Python 3.12.3 / Linux，pytest 9.1.1

## 最终结果

```
python3 -m pytest -v
tests/test_api.py ............        [ 22%]
tests/test_pwm.py ...........         [ 44%]
tests/test_scan.py ...........        [ 67%]
tests/test_sequence.py .......        [ 81%]
tests/test_significance.py ........   [100%]
49 passed, 1 warning in 0.25s
```

覆盖率（`pytest --cov=app`）：**97%**（498 条语句，13 条未覆盖，均为防御性
分支，如 `config.py` 环境变量解析的异常路径、`provenance.py` 文件建库分支）。

## 评审记录（代码写完后执行）

- 曾尝试委派评审子代理，其运行环境权限不足且超时，已终止并改为人工复核
  （静态审查全部 `app/` 模块）。
- 复核发现并修复（MEDIUM）：p 值阈值不可达时响应会序列化非标准 JSON 的
  `Infinity` → 改为 `score_threshold: null` 并补测试断言。
- 复核发现并修复（MEDIUM）：FastAPI 模式校验失败与未预期异常不走统一错误
  信封 → 新增 `RequestValidationError`（类别 `INVALID_REQUEST`，422）与
  兜底 `Exception`（类别 `INTERNAL`，500，日志含 `request_id`）处理器，
  并补测试。
- 复核确认无问题：伪计数/log 优势比公式、尾概率与阈值反解、Bonferroni/BH
  实现、反向互补坐标映射、SQL 全部参数化（无注入面）、无硬编码密钥、无
  调试输出残留。

## 过程中出现并已修复的失败（保留记录）

首轮运行 2 失败 / 46 通过，两处均为**测试期望值本身写错**，核心实现无误：

1. `test_pwm.py::test_log_odds_match_hand_computed_values` — 测试把列序
   A,C,G,T 中 C 的索引误写为 2（实为 1），且位置 1 的非共识列集合写错。
   修正测试期望值后通过。
2. `test_sequence.py::test_ambiguity_letters_become_unknown_n` — IUPAC 模糊
   字母共 11 个（NRYSWKMBDHV），测试误写为 10 个。修正后通过。

## 冒烟测试（真实服务，非 TestClient）

以 `uvicorn app.main:app --port 8931` 启动真实服务，用 curl 验证：

- `POST /v1/scan`（`examples/scan_request.json`）：9 个命中，日志含
  `request_id` 与全部处理步骤（解析 → PWM → 枚举 → 阈值 → 扫描 → 持久化）。
- 非法序列（含数字）：422 `INVALID_SEQUENCE`，失败记录持久化并可经
  `GET /v1/scan/{request_id}` 回放（`status: "failed"`）。
- 冒烟期间发现 INFO 日志未输出（logger 无 handler），已在 `app/main.py`
  配置日志处理器并复验通过。

## 未执行项

- 未做性能/压测（精确枚举复杂度 4^k，k≤10 已在上限内验证；更大规模不在
  支持范围，见 README"支持范围与关键取舍"）。
- 未做并发写入 SQLite 的压力验证（单文件本地部署定位，README 已声明）。
- 警告项：starlette 提示 `httpx` 与 TestClient 组合的弃用警告
  （`StarletteDeprecationWarning`），仅测试路径受影响，功能正常，未处理。
