# 测试运行报告

日期：2026-10-04　环境：Python 3.12.3 / Linux 6.8，依赖版本见 `requirements-lock.txt`。

## 最终结果

```
python3 -m pytest -v
======================== 36 passed, 1 warning in 1.46s =========================
```

- **36 通过，0 失败，0 跳过。**
- 唯一警告：`StarletteDeprecationWarning`（starlette 测试客户端建议改用 `httpx2`），
  来自第三方库，不影响断言，未处理。
- 覆盖率（`pytest --cov=commit_reveal`）：**总计 90%**。未覆盖项说明见下。

## 开发过程中出现并修复的失败（保留记录）

初次全量运行为 `3 failed, 33 passed`，三处失败及处置：

1. `test_replay.py::test_verifier_cli_agrees` — 子进程 CLI 找不到包
   （`ModuleNotFoundError: commit_reveal`）。原因：子进程未继承 `src` 的
   PYTHONPATH。修复：测试中显式构造 `env` 传入 `src` 绝对路径。
2. `test_replay.py::test_replay_from_scratch_same_inputs` — 第二轮回放提交报
   `LateCommitmentError`。原因：共享的 ManualClock 已被第一轮推进到揭示截止之后。
   修复：第二轮开始前将时钟重置到 T0。
3. `test_abstention.py::test_abstention_changes_outcome_vs_full_reveal` — 原断言
   假设弃权必然改变三人夹具的排名，但该宇宙中弃权后排名恰好与全揭示排名
   过滤后一致（获胜者都是 bob），断言不成立。修复：改用 5 人 `flip_demo`
   冻结向量（参考实现证实 carol 弃权使获胜者 bob→dave），断言具体获胜者变化。

## 未执行 / 未覆盖项

- `api/app_factory.py`（覆盖率 0%）：模块级 ASGI app 工厂，单测不覆盖；
  已通过真实启动 `uvicorn` + `scripts/demo_flow.py` 做了端到端冒烟
  （建轮→承诺→揭示→定稿→离线验证全部 PASS，获胜者 carol，证据 8/8 检查通过）。
- `verify/independent.py` 的 CLI `main()`（66% 覆盖）：由子进程测试
  `test_verifier_cli_agrees` 实际执行，但子进程不计入覆盖率统计。
- `service.py` 少数输入校验分支（非法 hex 长度、非法轮次参数）未逐一覆盖。
- 未做并发压力测试与数据库损坏恢复测试（超出本次范围）。

## 参考答案独立性说明

- `tests/fixtures/reference_vectors.json` 由 `tests/helpers/reference_impl.py`
  （仅标准库 hashlib/hmac，HKDF 为手写 RFC 5869）一次性生成后冻结为常量；
  被测核心（cryptography / PyCryptodome 适配层）必须匹配这些常量。
- 测试另在运行时用参考实现交叉验证种子与排名，双重锚定。
