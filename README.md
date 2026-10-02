# stunlab — 本地受控 STUN Binding 请求/响应实验台

一个**仅限受控 UDP 测试**的 STUN（RFC 5389/8489 子集）Binding 请求响应服务与客户端，
外加一个用**独立语言（Python）从零实现的协议预言机**作为交叉验证参考。

> 范围明确：只实现 Binding、`XOR-MAPPED-ADDRESS`、`MESSAGE-INTEGRITY`（HMAC-SHA1，
> 短期共享密钥）、`ERROR-CODE`/`UNKNOWN-ATTRIBUTES`（420）。
> **不实现 TURN 中继**、长期认证（USERNAME/REALM/NONCE 握手）、FINGERPRINT 发送。
> 所有参与者都是本地合成的 loopback 端点，无需任何生产账号或真实业务数据。

## 能力对照（需求 → 实现）

| 需求 | 实现位置 |
|------|----------|
| 属性长度 / 四字节填充正确 | `internal/stun/attributes.go`（`EncodeAttributes`/`DecodeAttributes`） |
| 未知必须理解属性明确报错 | `UnknownRequired` + 服务端 420（`internal/server/server.go`） |
| XOR 映射地址按事务与魔数计算 | `EncodeXORMappedAddress`/`DecodeXORMappedAddress` |
| 事务超时 + 响应源匹配，旧响应不能完成新请求 | `internal/client/transaction.go`、`client.go` |
| 消息完整性使用成熟 HMAC | `internal/stun/integrity.go`（`crypto/hmac` + SHA1） |
| 字节编解码 / 状态机 / 受控服务 / 兼容测试分层 | `internal/stun`、`internal/client`、`internal/server`、`test/cross` |
| 输入错误/状态冲突/资源耗尽/计算失败可区分 | `internal/stun/errors.go` 的 `ErrorKind` |
| 运行编号 + 中间状态 + 判断理由可重放 | `internal/evidence`（JSONL）+ `internal/store`（SQLite） |
| 对照独立编码器，答案非被测实现自产 | `test/oracle/stun_oracle.py` + 冻结夹具 `test/testdata/fixtures.json` |
| 最小数据夹具 / 调用示例 / 依赖锁定 / 复现文档 | `test/testdata/`、下文、`vendor/`+`go.sum`、`docs/REPRODUCIBILITY.md` |

## 目录结构

```
cmd/stund/            受控服务端可执行程序
cmd/stunc/            客户端可执行程序
internal/stun/        协议核心：消息/TLV/地址/XOR/HMAC/错误分类
internal/client/      客户端 + 事务表状态机（超时/源匹配/一次性投递）
internal/server/      受控 UDP 响应服务（200/400/420/静默丢弃）
internal/store/       SQLite 证据持久化（runs/events/exchanges）
internal/evidence/    run 级结构化 JSONL 日志（run_id + 单调 seq）
test/oracle/          独立 Python STUN 预言机（从零实现，无共享代码）
test/cross/           Go↔Python 跨语言真实 UDP 互操作测试
test/testdata/        预言机生成的冻结已知答案夹具
scripts/run_demo.sh   一键真实运行（正常 + 异常）并导出证据
docs/                 复现文档、协议与错误契约
vendor/               锁定的依赖（go-sqlite3 v1.14.52），完全离线构建
```

## 快速开始（完全离线）

需要 Go 1.22（含 cgo 用的 gcc）与 Python 3（仅测试/预言机使用，均为标准库）。

```bash
make build          # 离线构建 bin/stund bin/stunc
make fixtures       # 用独立 Python 预言机（重新）生成冻结夹具
make test           # 全部单元 + 跨语言互操作测试
make cover          # 竞态检测 + 覆盖率
make demo           # 真实 loopback 运行（正常/异常）并落证据
```

## 服务调用示例

终端 A —— 启动带完整性保护的服务端（IPv4）：

```bash
./bin/stund -net udp4 -addr 127.0.0.1:3478 -key lab-shared-secret \
  -db evidence/stund.db -log evidence/stund.jsonl -note "manual run"
```

终端 B —— 客户端发起一次 Binding：

```bash
$ ./bin/stunc -server 127.0.0.1:3478 -key lab-shared-secret
run_id=stunc-... txn=88f2db... server=127.0.0.1:3478 endpoint=127.0.0.1:48605 family=ipv4 attempts=1
```

IPv6：

```bash
./bin/stund -net udp6 -addr "[::1]:3478" -key lab-shared-secret
./bin/stunc -net udp6 -server "[::1]:3478" -key lab-shared-secret
```

异常路径与退出码：

| 场景 | 命令/现象 | 退出码 | 错误类别 |
|------|-----------|--------|----------|
| 错误密钥 | 服务端按 RFC 静默丢弃，客户端重传后超时 | 3 | `timeout`（服务端记 `integrity_failure`） |
| 无响应端口 | `-server 127.0.0.1:<silent>` | 3 | `timeout` |
| STUN 错误响应 | 收到 4xx/5xx | 2 | 打印 code/reason |
| 完整性失败 | 响应被篡改/缺 MI | 2 | `integrity_failure` |
| 本地参数错误 | 无法解析地址等 | 4 | `input_error` |

## 证据（可重放）

每次运行都有唯一 `run_id`，事件同时写入 JSONL（每行含 `run_id`、单调 `seq`、
`event`、关键字段、`error_kind`、`verdict`/`reason`）与 SQLite 三张表
（`runs` / `events` / `exchanges`）。`make demo` 产出目录
`evidence/runs/<run-id>/`，内含 transcript、所有 jsonl/db，可直接复核：

```bash
sqlite3 evidence/runs/<run-id>/stund4.db \
  "SELECT outcome, count(*) FROM exchanges GROUP BY outcome;"
```

## 文档

- [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md) — 一步步复现与已记录结果
- [docs/PROTOCOL.md](docs/PROTOCOL.md) — 协议子集、模块数据/错误契约
- [docs/FIXTURES.md](docs/FIXTURES.md) — 夹具向量与独立预言机说明
