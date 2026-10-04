# Reproduce / 复现指南

本目录保存可复核的运行结果与复现步骤。所有输入均为合成数据，无外部依赖。

## 1. 环境

- Python 3.12（仅使用标准库 + 下列锁定依赖）
- 建立本地虚拟环境并安装**精确锁定**版本：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip freeze > docs/requirements-lock.txt   # 已随仓库提供一份
```

## 2. 生成带外夹具（固定密钥 / 已知答案）

```bash
python scripts/generate_fixtures.py
```

产出（已提交，内容确定可复现）：

- `fixtures/test-keys.json`：固定测试密钥（仅测试用，文件内有 warning）
- `fixtures/vectors.json`：6 组合成消息、不同分块的帧、明文与 SHA-256、
  以及用**另一个** AEAD 库打开的分段结果
- `fixtures/gcm-kat.json`：固定 key/nonce/AAD/明文的 AES-256-GCM 已知答案，
  两个后端必须逐字节一致

这些答案不经过被测的接收服务生成，是独立的比对基准。

## 3. 运行测试并留存结果

```bash
pytest -q | tee docs/test-results.txt
pytest --cov=app --cov-report=term-missing | tee docs/coverage-results.txt
```

## 4. 真实运行（正常路径，含乱序上传）

```bash
# 终端 A：启动服务
python -m app.core.keyring init ./fixtures/demo-keys.json
SSEA_KEY_FILE=./fixtures/demo-keys.json SSEA_DB_PATH=./data/demo.sqlite3 \
  python -m app

# 终端 B：正常顺序
python scripts/client_example.py --message-id run-ordered --length 5000 --chunk-size 1024
# 终端 B：乱序顺序（自动校验字节一致并打印 BYTE-IDENTICAL: True）
python scripts/client_example.py --shuffle --message-id run-shuffled | tee docs/demo-results.txt
```

## 5. 异常路径人工验证（失败不得输出假完整消息）

```bash
# 缺终止标记 / 篡改 / 删段 / nonce 重用等都由自动化测试断言：
pytest -q tests/integration/test_service.py tests/integration/test_http_api.py
```

预期：成功用例最终 `BYTE-IDENTICAL: True`；任何失败用例服务都不产生 released
产物，审计中可按 `x-request-id` 查到 `accept / reject / inconclusive` 及原因。

## 6. 进程中断恢复

测试 `test_process_interruption_is_recoverable` 模拟：进程 1 上传一半后退出，
进程 2 用同一 SQLite 与暂存目录启动 → 旧流被标记 `interrupted`（无法判定，不
发布），客户端重发缺失段（前 4 段幂等重放）后完成，明文逐字节一致。
