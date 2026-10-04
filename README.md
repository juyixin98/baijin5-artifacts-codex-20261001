# 本地敏感字段随机密文 + 带密钥盲索引查询层

一套多模块本地后端：敏感字段以**随机化 AES-256-GCM** 加密存储，另建**带密钥盲索引**
（HMAC-SHA256 截断）支持等值查询；索引命中后**必须解密二次确认**才返回结果。
所有数据与密钥均为本地合成夹具，不依赖任何生产账号或真实业务数据。

## 安全性质与明确的非声明

- **密文是随机的**：同一明文每次加密产生不同密文（随机 96 位 nonce）。
- **盲索引是确定性的，会泄露相等关系**：任何能读到索引列的人都能判断哪些行
  取值相同，并可对低熵取值做字典攻击。它是检索辅助，**不是匿名化，也不是加密**，
  绝不能拿确定性索引当密文替代品。查询响应的 `notice` 字段也内置了这一提示。
- **规范化是索引身份的一部分**：`" Alice@Example.COM "`、全角 `ａｌｉｃｅ＠…` 等
  不同表示规范化为同一形式，产生同一索引。规范化版本号混入索引消息，规则变更
  不会与旧索引静默混叠。
- **用途域隔离**：purpose 与规范化版本通过长度前缀帧绑定进索引消息，同一明文
  在不同 purpose 下索引无关，不可跨域重放。
- **密钥与密文同库存储仅是本地夹具简化**（见 `crypto/keystore.py` 文档字符串），
  生产环境必须改用 KMS/HSM。
- **审计日志只输出记录身份**（record_id）、请求身份、密钥版本与计数；取值、
  规范化结果、索引摘要、密文在 `audit/auditlog.py` 的白名单边界被强制拒绝。

## 模块划分

| 模块 | 职责 |
|---|---|
| `sensitive_layer/protocol/` | 规范化（NFKC/casefold/数字提取）与域分隔长度前缀帧 |
| `sensitive_layer/crypto/` | 成熟密码适配：AES-256-GCM（cryptography）、HMAC-SHA256 盲索引（PyCryptodome）、版本化密钥存储 |
| `sensitive_layer/state/` | SQLite 模式、仓储、轮换状态机（崩溃可恢复） |
| `sensitive_layer/audit/` | 白名单强制的结构化审计日志 |
| `sensitive_layer/service.py` | 核心管线：写入（加密+索引）、查询（索引候选→解密确认）、轮换 |
| `sensitive_layer/api/` | FastAPI 表面：请求身份、trace、错误分类 |
| `verification/` | **独立验证**：仅用标准库的明文参考实现 + HTTP 驱动，不导入被测核心 |
| `tests/` | 独立测试：断言具体结果集与失败类别 |

## 复现步骤（从干净目录）

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

依赖版本（`requirements.txt` 已固定）：fastapi 0.142.2、uvicorn 0.54.0、
cryptography 50.0.2、pycryptodome 3.23.0、pytest 9.1.1、httpx 0.28.1；
Python 3.12。

### 运行测试

```bash
.venv/bin/python -m pytest -q
```

覆盖：同值不同表示、强制短索引碰撞（8 位索引下碰撞对被解密确认过滤）、
NULL 语义、轮换中断/恢复/重启续跑、域隔离、密文随机性、审计不含取值、
以及由标准库参考实现独立核对的端到端用例。

### 运行独立验证（真实 HTTP，标准库参考答案）

```bash
.venv/bin/python verification/run_verification.py
```

该脚本启动临时服务（16 位短索引以强制碰撞），用 `verification/reference.py`
（纯标准库，不导入被测核心）从明文夹具独立推导期望结果，逐项比对：
查询结果集、索引确定性（stdlib HMAC 重算）、NULL 拒绝类别、轮换中断前后
结果一致性。全部通过时退出码为 0 并输出 `N passed, 0 failed`。

夹具由 `verification/gen_fixtures.py` 生成（碰撞对由标准库参考实现生日搜索
得出）；更换 `config/settings.verify.json` 密钥后需重新生成。

### 启动服务与请求样例

```bash
.venv/bin/python -m uvicorn sensitive_layer.api.main:app --port 8000
# 或指定配置：SENSITIVE_LAYER_CONFIG=config/settings.dev.json
```

写入（同值的不同表示）：

```bash
curl -X PUT localhost:8000/records/r1 -H 'Content-Type: application/json' \
     -H 'X-Request-Id: demo-1' \
     -d '{"field":"email","purpose":"lookup:email","value":"  Alice@Example.COM "}'
```

查询（规范化后命中，`confirmed` 为解密确认后的结果，`trace` 展示关键步骤与版本）：

```bash
curl -X POST localhost:8000/query -H 'Content-Type: application/json' \
     -d '{"field":"email","purpose":"lookup:email","value":"alice@example.com"}'
# => {"ok":true,"request_id":"...","result":{"confirmed":["r1"],
#     "filtered_candidates":0,"uncertain":[],"index_versions_queried":[1],
#     "notice":"blind indexes are deterministic and leak equality ...",
#     "trace":["normalized","index-versions-queried=[1]","candidates=1",
#              "decrypt-confirm: confirmed=1 filtered=0 uncertain=0"]}}
```

索引密钥轮换（`crash_after` 为测试钩子，模拟处理 N 条后中断）：

```bash
curl -X POST localhost:8000/admin/rotate-index-key -d '{"crash_after":1}' -H 'Content-Type: application/json'
# 状态变为 rotating，期间查询自动使用新旧双版本，不漏记录
curl -X POST localhost:8000/query ...        # index_versions_queried == [1,2]
curl -X POST localhost:8000/admin/rotation/resume
curl      localhost:8000/admin/rotation/status
```

其他端点：`GET /records/{id}?field=&purpose=&include_plaintext=true`
（明文回读受 `allow_plaintext_read` 配置门控）、`GET /admin/indexes?...`
（开发用索引内省）、`GET /audit`（审计条目，仅记录身份）。

## 失败类别（错误响应 `error.category`）

| category | HTTP | 含义 |
|---|---|---|
| `validation_error` | 422 | 输入非法或规范化后为空 |
| `unknown_purpose` | 422 | purpose 未在配置注册 |
| `null_not_indexable` | 422 | NULL 不可索引、不可查询 |
| `record_not_found` | 404 | 记录不存在 |
| `rotation_conflict` | 409 | 轮换进行中/无轮换可恢复 |
| `plaintext_read_disabled` | 403 | 配置禁止明文回读 |

查询结果中无法解密确认的候选单独列入 `uncertain`（含原因），不计入
`confirmed` 也不静默丢弃。

## 配置说明

`config/settings.dev.json`（64 位索引）与 `config/settings.verify.json`
（16 位索引，供验证强制碰撞）。字段：`index_bits`（8–256）、`norm_version`、
`purposes`（purpose → 字段类型 email/phone/name/raw）、`keys`（版本化十六进制
密钥，**仅本地开发**）、`active_enc_version` / `active_index_version`、
`allow_plaintext_read`。轮换产生的新索引密钥持久化于 SQLite `keys` 表。
