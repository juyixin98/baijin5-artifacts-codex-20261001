# SCRAM-SHA-256 本机测试认证服务

仅用于**本机测试账号**的 SCRAM-SHA-256 / SCRAM-SHA-256-PLUS 客户端与服务器状态机实现
（[RFC 5802](https://www.rfc-editor.org/rfc/rfc5802) + [RFC 7677](https://www.rfc-editor.org/rfc/rfc7677)）。
所有账号均为本地合成夹具，不依赖任何生产账号或真实业务数据；服务默认只绑定 `127.0.0.1`。

## 1. 已实现的验收规则

| 规则 | 实现位置 | 说明 |
|---|---|---|
| nonce 拼接 | `src/scram_auth/client.py`、`server.py` | 服务器 nonce 必须以客户端 nonce 为**严格前缀**且非空扩展；final 轮 nonce 必须逐字节等于服务器拼接值；已用客户端 nonce 做 SHA-256 去重，TTL 窗口内重放判为 `nonce-replay` |
| 属性顺序 / 重复属性 | `src/scram_auth/wire.py` | `n,r`、`r,s,i`、`c,r,p`、`v` 必须按语法顺序出现；重复属性、未知属性、`m=` 强制扩展一律拒绝 |
| 通道绑定范围明确 | `wire.py`、`server.py`、`config.toml` | 支持范围：非 PLUS（GS2 `n`/`y`）与 `p=tls-server-end-point`（RFC 5929 证书哈希）。**不支持** `tls-unique`/`tls-exporter`，出现即 `unsupported-channel-binding`；PLUS 模式下拒绝降级到非 PLUS |
| 只存加盐验证材料 | `src/scram_auth/verifiers.py` | SQLite 仅保存 `salt / iteration_count / StoredKey / ServerKey`；**不保存明文密码、SaltedPassword 或 ClientKey**（测试 `test_database_contains_no_plaintext_password` 扫描整库断言） |
| 双向证明验证 | `client.py`、`server.py` | 服务器恢复 ClientKey → 哈希比对 StoredKey 验证客户端证明（`invalid-proof`）；客户端用 ServerKey 验证 `v=` 服务器签名（`server-signature-invalid`），所有比对走 `hmac.compare_digest` |
| 失败会话不复用中间状态 | 两端状态机 | 成功/失败均为终态；再次提交判为 `session-reuse`，终态时即清除密钥材料；会话有 TTL |
| 成熟密码适配 | `src/scram_auth/crypto.py` | PBKDF2-HMAC-SHA-256 经 **PyCryptodome** 实现；迭代数下限强制 4096（RFC 7677 §4）；密码/用户名先做 **SASLprep（RFC 4013）** |

## 2. 目录结构（真实模块，非单文件脚本/桩）

```
config/config.toml              独立配置（绑定地址、迭代数、CB 模式、限额、TTL）
src/scram_auth/
  errors.py      稳定的失败分类 FailureCategory（协议/编码/证明/重放/生命周期……）
  config.py      TOML 配置加载与启动期 fail-fast 校验
  saslprep.py    RFC 4013 SASLprep（基于 stdlib stringprep）
  crypto.py      PyCryptodome 密码原语与 SCRAM 密钥派生
  wire.py        严格线消息语法、GS2 头、base64、通道绑定数据
  verifiers.py   加盐验证材料 + SQLite 仓储（无明文）
  audit.py       关联 ID 贯穿的结构化 JSONL 审计
  client.py      客户端状态机（一次性，终态不可复用）
  server.py      服务器状态机（重放表、TTL、终态、防用户枚举时序）
  app.py         FastAPI 适配层（关联 ID、错误信封、滑动窗口限流）
  main.py        uvicorn 入口
scripts/
  provision_account.py  开通本机测试账号（密码不落盘）
  demo_client.py        HTTP 端到端示例调用
  run_server.sh         启动脚本
tests/
  fixtures/rfc7677_vector.json  RFC 7677 Appendix 3 固定向量（含独立推算的中间密钥）
  _oracle_stdlib.py             独立 stdlib 参考预言机（hashlib/hmac，与生产代码零共享）
  test_wire.py / test_crypto.py / test_saslprep.py /
  test_state_machine.py / test_channel_binding.py /
  test_http_api.py / test_config.py / test_fixture_integrity.py
```

## 3. 快速开始

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt          # 已全部锁定版本

# 开通一个本机测试账号（密码提示输入，仅用于派生，不保存）
python scripts/provision_account.py -u user        # 输入 pencil

# 启动（仅监听 127.0.0.1:8765）
./scripts/run_server.sh

# 另一个终端：端到端示例调用
SCRAM_TEST_PASSWORD=pencil python scripts/demo_client.py -u user
```

### 复现 RFC 7677 固定向量

```bash
python -m pytest tests/test_state_machine.py::TestRfc7677FixedVector -v
```

该用例用 RFC 公布的 `user/pencil` 线消息，配合**固定注入的服务器 nonce 片段**与 RFC 盐值，
逐字节断言 client-first / server-first / client-final（含 `p=`）/ server-final（含 `v=`）。

## 4. HTTP 接口

| 方法/路径 | 作用 |
|---|---|
| `GET /healthz` | 版本、机制、通道绑定支持范围、迭代数、处理位置 |
| `POST /v1/auth/scram/first` | body `{"message": "n,,n=user,r=..."}` → `{session_id, server_first}` |
| `POST /v1/auth/scram/final` | body `{session_id, message: "c=..,r=..,p=.."}` → `{username, server_final: "v=.."}` |

请求可带 `X-Request-ID`（8–64 位字母数字及 `-_.`）；不合规时服务器重新生成并在审计中记一条
`uncertain` 结论。所有响应都回带 `X-Request-ID` 头。

失败统一信封：

```json
{"success": false, "request_id": "…",
 "error": {"category": "invalid-proof", "message": "…", "detail": {…}}}
```

| category | HTTP | 含义 |
|---|---|---|
| `protocol-violation` / `invalid-encoding` | 400 | 顺序、重复、语法、base64 错误 |
| `unsupported-mechanism` | 400 | `m=` 等未实现扩展 |
| `unsupported-channel-binding` | 501 | 超出明确支持范围（如 tls-unique） |
| `channel-bindings-dont-match` | 401 | CB 数据/哈希不匹配或非法降级 |
| `invalid-proof` / `nonce-replay` / `nonce-mismatch` | 401 | 凭证失败 / 重放 / nonce 被换 |
| `server-signature-invalid` | 401 | 客户端侧验证服务器签名失败 |
| `session-not-found` / `session-expired` | 404 | 会话未知或超 TTL |
| `session-reuse` | 410 | 终态会话再次提交 |
| `weak-parameters` | 400 | 迭代数低于客户端下限 |
| `rate-limited` | 429 | 同类失败超过滑动窗口（默认 20 次/60 秒/对端） |

## 5. 可解释性（日志）

审计写入 `logs/audit.jsonl`，每行一个 JSON，字段包含：
`ts / component / version / request_id / session_id / event / step / location / outcome`，
失败另列 `failure: {category, message, detail}`，不确定结论另列 `uncertain: [...]`。
一次成功认证可用同一个 `request_id` 串起：`http_request → server_first_sent →
client_proof_verified(success)`。**密码、proof、StoredKey/ServerKey、签名字节均不记录**，
只记长度与布尔结论。查看：

```bash
grep '"request_id": "demo-' logs/audit.jsonl | python -m json.tool
```

## 6. 测试与独立验证

```bash
python -m pytest                                  # 全量
python -m pytest --cov=scram_auth --cov-report=term-missing
```

关键设计：**参考答案不是由被测核心自己生成的**。

- `tests/fixtures/rfc7677_vector.json`：线消息取自 RFC 7677 Appendix 3 原文；
  中间密钥（SaltedPassword/ClientKey/StoredKey/ServerKey/Client(Server)Signature/Proof）
  在交付前用三条独立路径算出并一致：CPython `hashlib`/`hmac`、PyCryptodome `PBKDF2`、
  `openssl kdf PBKDF2` 命令行。
- `tests/_oracle_stdlib.py`：测试内的独立预言机，只使用标准库，**不 import 任何生产密码代码**；
  `test_fixture_integrity.py` 运行时再次现场重算整条密钥链与夹具互相校验，防止夹具被篡改后自证。
- 负向测试断言**具体类别**：篡改 final proof → `invalid-proof`、篡改 `v=` →
  `server-signature-invalid`、nonce 重放 → `nonce-replay`、终态复用 → `session-reuse`、
  跨会话提交 → 不允许认证成功、12 路线程池并发 → 会话彼此隔离。

## 7. 通道绑定配置

`config/config.toml`：

- `mode = "none"`（默认）：只提供非 PLUS；客户端发 `p=` 会得到 `unsupported-channel-binding`。
- `mode = "tls-server-end-point"`：要求 PLUS；HTTP 调用需在
  `X-Tls-Server-End-Point-Sha256` 头携带服务器证书的 32 字节 SHA-256（hex）。
  本服务自身不终结 TLS，该哈希由（本地测试）部署边界注入；哈希不一致 →
  `channel-bindings-dont-match`。

## 8. 剩余限制（如实说明）

1. 服务为明文 HTTP + 头注入证书哈希的**测试形态**；生产部署必须在真实 TLS 终结层提取
   tls-server-end-point 摘要后传入，PLUS 安全保证才成立。
2. 未实现 `tls-unique` / `tls-exporter`、SCRAM 通道绑定协商标志 `y` 的服务端升级推送
   （接受 `y` 但按非 PLUS 处理），也未实现 `m=` 扩展与 SCRAM 其他哈希族。
3. 会话/重放/限流状态为单进程内存态，重启即清空；不适合多副本水平扩展。
4. 审计日志为本地 JSONL 追加文件，无签名/转发；数据库是本机 SQLite（WAL）。
5. 防用户名枚举采用等成本 dummy 派生，但 Python 层时序仍可能有调度抖动，不提供恒定时间保证。
