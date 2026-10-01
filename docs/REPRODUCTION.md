# 复现文档

所有步骤只依赖本机 loopback 与合成数据，不需要任何生产账号或外网 STUN。

## 0. 环境与依赖锁定

- Go 1.22+；SQLite 用纯 Go 驱动 `modernc.org/sqlite v1.34.5`，无 cgo。
- 交叉验证用 Python 3（只用标准库：hmac/hashlib/zlib(binascii)/struct/json）。
- 依赖版本由 `go.mod`/`go.sum` 锁定，可用以下命令校验与离线构建：

```bash
go mod verify          # 校验模块缓存与 go.sum 一致
GOPROXY=off go build ./...
python3 --version
```

## 1. 单元 / 竞争 / 覆盖率

```bash
GOPROXY=off go test -race -count=1 ./...
GOPROXY=off go test -cover ./internal/... ./test/compat/
```

## 2. RFC 5769 已知答案

`testdata/vectors/rfc5769_2_{1,2,3}_*.hex` 是 RFC 5769 的逐字节报文。
`internal/stun/codec_test.go` 中：

- `TestRFC5769_2_1_RequestKnownAnswer`：验 HMAC/FINGERPRINT，解析 USERNAME、
  SOFTWARE、PRIORITY。
- `TestRFC5769_2_2/2_3`：验完整性并还原 IPv4/IPv6 映射地址。
- `TestXORAddressRFCWireBytes`：自编码必须逐字节等于 RFC 公布的 XOR 串。

## 3. 独立预言机双向兼容

```bash
# 仅看预言机自生成并自判（应 14/14）
python3 test/compat/oracle/stun_oracle.py emit > /tmp/cases.json
python3 test/compat/oracle/stun_oracle.py judge < /tmp/j.ndjson   # judge 读 JSONL

# Go 测试驱动双向对照（推荐）
GOPROXY=off go test ./test/compat/ -v
```

- `TestPythonVectorsDecodeInGo`：Python 独立生成向量 → Go 解码判同。
- `TestGoVectorsJudgedByPython`：Go 生成向量 → Python 独立解码判同。
- `TestRFC5769AgreesWithOracle`：Go 重编码 RFC 5769 风格报文，Python 也须通过。

若环境没有 python3，该测试套件 `t.Skip` 跳过（Go 侧 RFC KAT 仍然生效）。

## 4. 端到端真实运行（正常 + 异常）

```bash
bash scripts/run_e2e.sh
```

脚本会：构建三个二进制 → 在 `127.0.0.1:0` 起真实服务器（SQLite 审计）→
3 次正常 Binding → 7 种注入 → 尝试一次 IPv6 → 汇总审计库。

预期注入判定（`stuninject -expect`）：

| mode             | 期望结果   | 说明                                   |
|------------------|------------|----------------------------------------|
| valid            | success    | 签名请求，返回 XOR-MAPPED-ADDRESS      |
| unsigned         | error401   | 签名服务端拒绝无 MI 请求               |
| unknown-required | error420   | 未知必选属性，回 UNKNOWN-ATTRIBUTES    |
| unknown-optional | success    | 已签名且带未知可选属性，应被容忍       |
| junk             | dropped    | 不足 20 字节，静默丢弃                 |
| bad-cookie       | dropped    | 魔数错误，静默丢弃                     |
| tampered         | dropped    | 翻转 FINGERPRINT 字节，HMAC/CRC 失败   |

退出码 0 表示所有判定一致；输出末尾应为
`REPRODUCTION RESULT: ALL JUDGMENTS MATCH`。

## 5. 产物与审计重放

运行后 `results/` 包含：

- `audit.db`：SQLite 审计库（表 `audit_events`，run/seq 有索引）。
- `e2e_server.log`：绑定地址与 run id。
- `e2e_client_normal.log` / `e2e_client_v6.log`：正常客户端输出。
- `e2e_client_events.jsonl`：客户端状态机事件（含超时/源不符等类别）。
- `e2e_anomalies.log`：每条注入的结果与 JUDGMENT。
- `e2e_summary.txt`：按事件/类别聚合计数与前若干行可重放记录。

手工查询某次 run：

```bash
sqlite3 results/audit.db \
 "SELECT seq,event,kind,substr(tx_id_hex,1,12),src_addr,detail \
  FROM audit_events WHERE run_id='<RUNID>' ORDER BY seq;"
```

每行含原始请求/响应十六进制（`wire_hex`，以 ` -> ` 分隔），可直接把请求
部分拷回 `stuninject`/Wireshark 重放。列出全部 run：

```bash
sqlite3 results/audit.db "SELECT run_id,COUNT(*) FROM audit_events GROUP BY 1;"
```

## 6. 故障类别如何区分（对照需求）

| 需求中的类别     | 实现位置/可观察结果                                    |
|------------------|--------------------------------------------------------|
| 输入错误         | `kind=input`；如 bad-cookie/junk/截断/长度不符         |
| 状态冲突         | 客户端 `state`；源不符、无主事务、ctx 取消             |
| 资源耗尽         | `exhausted`；事务超时、事务表满、超长数据报            |
| 计算失败         | `compute`；携带 MI 却无校验密钥、随机源/socket 错误    |
| 完整性失败       | `integrity`；HMAC/FINGERPRINT 不符、未知必选属性(420)  |

## 7. 明确范围

不实现 TURN 中继（RFC 5766）。注入器/服务端默认密钥仅用于受控测试，
不得用于任何真实部署。
