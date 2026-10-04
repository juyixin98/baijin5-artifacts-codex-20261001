# 复现指南

## 环境

- Python 3.12（>= 3.11 即可），Linux
- 全部依赖锁定于 `requirements.txt`（含传递依赖的精确版本）

## 步骤

```bash
cd <repo>
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 1) 重新生成参考测试向量（PyCryptodome 独立生成，可与仓库内文件比对）
PYTHONPATH=. .venv/bin/python scripts/make_fixtures.py
git diff --exit-code tests/fixtures/vectors.json   # 应当无差异

# 2) 运行测试（53 个用例，含正常与异常路径）
.venv/bin/python -m pytest -v

# 3) 真实启动服务并跑端到端示例
export SAE_MASTER_KEY=$(python3 -c "import os; print(os.urandom(32).hex())")
export SAE_PORT=8391            # 任选空闲端口
.venv/bin/python -m sae &       # 另开终端执行下一步，或后台运行
SAE_URL=http://127.0.0.1:8391 .venv/bin/python examples/client_demo.py
```

## 预期结果

- `pytest`：**53 passed**（实测见 `docs/TEST_RESULTS.md`）。
- demo：乱序提交 2 段 → finalize 接受 → 发布 8000 字节且 sha256 与原文一致；
  未完成流 finalize 返回 `409 incomplete_stream`；审计轨迹逐条打印。

## 测试覆盖的异常场景（tests/）

| 文件 | 场景 | 断言 |
|---|---|---|
| `test_roundtrip.py` | 分块 1/3/7/1024/65536 字节、乱序提交、空消息、单段消息 | 解密字节与原文完全一致 |
| `test_tamper.py` | 删中段、截尾、缺终止标记、终止标记错位/重复、密文翻位、暂存段换序 | 抛出对应类别异常，且无明文发布 |
| `test_idempotency.py` | 同内容重试、不同内容同序号、final 翻转重试 | 幂等返回已存密文 / `nonce_reuse_conflict` |
| `test_crash_recovery.py` | 发布事务前崩溃、分段中途崩溃后重启 | 不泄露明文，重启后可完成 |
| `test_vectors.py` | 独立 fixture 向量、tag 翻位、序号/final 绑定 | 核心实现逐比特复现参考密文 |
| `test_protocol.py` | AAD/nonce 黄金字节、RFC 5869 HKDF 向量 | 与手写/公开参考值相等 |
| `test_api.py` | HTTP 全流程、错误分类、请求标识、审计脱敏 | 状态码与 category 精确匹配 |

## 数据夹具

`tests/fixtures/vectors.json`：两条消息（多段含空终止段、单段全字节域），
含 msg_id/salt/nonce_base/派生密钥/每段 AAD/nonce/明文/密文。
由 `scripts/make_fixtures.py` 用 **PyCryptodome** 生成——与被测核心
（`cryptography`）不是同一实现，保证参考答案独立。
