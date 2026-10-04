# trustlab — 本地 TLS 双向认证信任包切换测试平台

一个完全本地化的 mTLS 信任包（trust bundle）轮换测试平台。数据平面是基
于 Python `ssl`（OpenSSL）的成熟 TLS 栈，控制平面是 FastAPI，状态与审
计落 SQLite，独立验证用 PyCryptodome 与 `openssl` CLI 交叉核对。所有
证书均由本地合成 CA 生成，**不连接任何生产服务，不使用真实业务数据**。

## 架构与模块边界

```
┌──────────────────────────── 控制平面 (HTTP, FastAPI) ───────────────────────────┐
│ trustlab/control.py   信任包管理 / 连接管理 / 审计查询 / 失败解释 (POST /explain) │
└──────────────┬───────────────────────────────────────────────┬─────────────────┘
               │ 组合根 trustlab/service.py                     │
┌──────────────▼──────────────┐                  ┌──────────────▼─────────────────┐
│ trustlab/bundle.py          │  on_activated    │ trustlab/tls_plane.py          │
│ 版本化信任包：轮换/回滚/重叠 │ ───────────────▶ │ mTLS 数据平面 (OpenSSL)         │
└──────────────┬──────────────┘                  │ 新连接按握手时点快照认证；       │
               │                                  │ 既有连接不受换根影响           │
┌──────────────▼──────────────┐                  └──────────────┬─────────────────┘
│ trustlab/store.py           │ ◀──────────── 状态与审计 ───────┘
│ SQLite：bundles/connections/│
│ audit/handshake_failures    │
└─────────────────────────────┘
支撑模块：
  trustlab/errors.py     四类错误类别 + 握手失败细类（全系统统一契约）
  trustlab/frames.py     数据平面帧协议（4 字节大端长度 + JSON），唯一编码点
  trustlab/certs.py      本地合成 CA/证书夹具（cryptography，RSA-2048/SHA-256）
  trustlab/identity.py   仅从已验证的 TLS 会话证书字段提取客户端身份
  trustlab/explainer.py  信任失败解释（含“无共同信任期”判定）
  trustlab/verify_ref.py 独立验证参考实现（PyCryptodome / openssl CLI / 有效期复核）
  trustlab/clientlib.py  测试与演示用的客户端助手（非服务端代码）
```

关键设计保证：

- **认证时点分离**：accept 路径为每个新连接原子快照
  `(SSLContext, bundle_version)`；激活新信任包只影响**之后**的握手。
  删除信任根**不会**自动撤销既有连接——既有连接被标记为 `legacy`
  并继续可用；撤销既有连接是独立的管理动作
  （`POST /connections/{id}/revoke`），审计中两类事件可区分。
- **客户端身份来源**：身份只从握手完成后的
  `SSLSocket.getpeercert(binary_form=True)` 提取（CN/SAN/签发者/序列号/
  指纹/EKU），应用层帧无法影响身份判定（`tests/test_identity.py` 验证）。
- **回滚不复用版本号**：版本号严格单调递增；回滚产生**新版本号**承载
  旧内容，历史版本永不改写。显式复用版本号返回 `STATE_CONFLICT`。
- **无共同信任期可解释**：`explainer` 从信任包历史重建根信任时间线，
  判定“是否存在过同时包含新旧根的版本”。`POST /explain` 接受客户端
  证书链（纯诊断输入，绝不用于认证）给出针对该客户端的解释。
- **独立验证**：核心判定（OpenSSL/`cryptography`）由 PyCryptodome
  （独立 RSA 验签实现）与 `openssl verify` CLI（独立进程）交叉核对；
  测试中的参考答案（接受/拒绝及失败类别）是手写的，不由被测核心生成。

## 错误语义

四类顶层错误在审计记录、HTTP 响应与帧错误中保持一致，可明确区分：

| 类别 | 含义 | HTTP | 典型触发 |
|------|------|------|----------|
| `INPUT_ERROR` | 输入无法解析/不合法 | 400 | 非法 PEM、非 CA 根证书、畸形帧、未知连接/版本号 |
| `STATE_CONFLICT` | 输入合法但与持久状态冲突 | 409 | 版本号复用、跳过版本号、重复撤销 |
| `RESOURCE_EXHAUSTED` | 达到配置的资源上限 | 429 | 连接数上限（握手前拒绝）、帧超长 |
| `CRYPTO_FAILURE` | 密码学验证/握手失败 | 422 | 证书过期、用途错误、不受信任的根 |

握手失败的细类（`handshake_failures.failure_class`，基于 OpenSSL
`verify_code` 映射）：

| failure_class | verify_code | 含义 |
|---------------|-------------|------|
| `CERT_EXPIRED` | 10 | 证书已过期 |
| `CERT_NOT_YET_VALID` | 9 | 证书尚未生效 |
| `UNTRUSTED_ROOT` | 18/19/20/21 | 链不到当前信任包中的根 |
| `WRONG_PURPOSE` | 26 | EKU 不含 clientAuth（TLS 层或应用层兜底拦截，`layer` 字段区分） |
| `SIGNATURE_INVALID` | 7 | 证书签名无效 |
| `NO_CLIENT_CERT` | — | 客户端未提供证书 |
| `HANDSHAKE_FAILED` | — | 其他握手失败 |

每条审计/失败记录都带 `run_id`、时间戳、关键中间状态（`detail`）与判
断理由（`reasoning`），可按 `run_id` 重放完整运行。

## 安装与运行

```bash
pip install -r requirements.txt

# 运行测试（实际执行真实 TLS 握手并报告结果）
python3 -m pytest tests/ -v

# 本地端到端演示（真实服务 + 完整轮换场景，打印逐项检查结果）
python3 scripts/demo.py

# 作为服务运行（控制平面 + 数据平面）
python3 -m trustlab --data-dir /tmp/trustlab --generate-fixtures \
    --port 8643 --control-port 8600
curl -s http://127.0.0.1:8600/bundles | jq
```

`--generate-fixtures` 会在 `<data-dir>/fixtures/` 生成本地服务器 CA、
服务器证书与一个客户端根 CA（含私钥，仅供本地测试）。

## 复现步骤（各失败场景）

以下均可用 `scripts/demo.py` 一键复现，或按步骤手动执行：

1. **重叠轮换**：初始 v1={旧根} → `POST /rotation/begin`（v2=旧+新）→
   新旧客户端都接受 → `POST /rotation/end`（v3=仅新根）→ 旧根客户端的
   **新**连接失败 `UNTRUSTED_ROOT`。
2. **过期证书**：用 `notAfter` 在过去的客户端证书连接 → 客户端收到
   TLS alert；服务端 `GET /failures` 记录 `CERT_EXPIRED`（verify_code=10）。
3. **错误用途**：仅含 serverAuth EKU 的证书用作客户端 → 记录
   `WRONG_PURPOSE`（OpenSSL 用途检查或应用层兜底，`layer` 字段标明）。
4. **旧连接保留**：在 v1 建立连接 → 轮换到 v3 → 旧连接仍可
   ping/whoami（`legacy=true`，`revoked=false`）→ 审计含
   “does not retroactively revoke” 说明 → 显式 revoke 后连接才关闭。
5. **回滚**：`POST /rollback {"to_version": 1}` → 产生 v(max+1)，
   内容同 v1；`explicit_version` 复用旧号 → 409 `STATE_CONFLICT`。
6. **无共同信任期**：`POST /bundles` 直接切换到从未共存过的新根
   （无重叠版本）→ 旧客户端失败 → `POST /explain` 返回
   `common_trust_period=false` 及完整理由链。

## 重放与审计查询

```bash
# 全部审计（含 run_id、判断理由）
curl -s http://127.0.0.1:8600/audit | jq
# 按类别过滤（四类错误可区分）
curl -s 'http://127.0.0.1:8600/audit?category=STATE_CONFLICT' | jq
# 握手失败记录（含 verify_code 与解释）
curl -s http://127.0.0.1:8600/failures | jq
# 直接查库重放某次运行
sqlite3 <data-dir>/trustlab.db \
  "SELECT ts, category, event, reasoning FROM audit WHERE run_id='<run_id>'"
```

pytest 运行时每个用例的 teardown 会打印该服务的完整审计轨迹（含
run_id），失败时可直接从日志重放。

## 测试清单（tests/）

| 文件 | 覆盖 |
|------|------|
| `test_rotation.py` | 重叠轮换全阶段握手结果与应用身份 |
| `test_failures.py` | 过期/错误用途/无证书/硬切换无共同信任期解释 |
| `test_legacy.py` | 旧连接保留、显式撤销、撤销错误语义 |
| `test_rollback.py` | 回滚新版本号、版本复用冲突 |
| `test_control_plane.py` | 错误类别↔HTTP 映射、资源耗尽、审计可区分性 |
| `test_identity.py` | 身份来自已验证证书、应用层无法伪造 |
| `test_independent.py` | PyCryptodome/openssl CLI 与核心判定一致性（手写参考答案） |

## 限制

- 证书夹具固定 RSA-2048/SHA-256（PyCryptodome 独立验签按此实现）。
- TLS 握手失败后服务端无法看到对端证书链（TLS 协议限制），因此实时
  失败记录的解释基于服务端信任包历史；针对具体客户端链的确定性解释
  走 `POST /explain` 诊断端点。
- 控制平面本身不鉴权（仅监听 127.0.0.1 的测试平台）；生产化需另加
  访问控制。
