# hpacklab — 可审查的 HPACK 头部压缩状态实现

一套从零实现的 HTTP/2 HPACK（[RFC 7541](https://www.rfc-editor.org/rfc/rfc7541)）
编码/解码后端，附带一个受控 HTTP/2 服务、SQLite 审计存储，以及使用**公开黄金
向量**与**独立参考实现**的兼容/集成测试。核心机制（动态表、字节成本驱逐、
Huffman、状态毒化、解压预算）全部由本仓库代码承担，没有硬编码演示答案。

## 模块关系

```
modules/
  codec/    字节编解码：5.1 前缀整数、5.2 字符串与静态 Huffman（无表语义）
  hpack/    协议状态机：静态表、动态表、四种表示、编/解码器、错误分类、观察者
  service/  受控 HTTP/2 服务：最小帧层 + 每连接 HPACK + SQLite + 结构化日志
  compat/   兼容测试：http2jp 公开黄金语料 + golang.org/x/net 独立 oracle
  itest/    独立黑盒集成测试：真实 h2c TCP 往返（隔离/截断/敏感字段/缩小）
configs/    服务 JSON 配置示例
scripts/    verify.sh 离线验证入口
docs/       算法假设、架构与日志说明
go.work     Go 工作区
```

依赖方向只允许自上而下：`codec ← hpack ← {service, compat, itest}`。
`compat` 额外依赖 `golang.org/x/net`，但**仅出现在 `_test.go`** 中作为独立
对照 oracle，生产代码不依赖它。

## 快速开始（全部离线）

前置：Go 1.22+；依赖需在本地模块缓存中（见“依赖版本”）。

```bash
# 一键：gofmt + 各模块 build/vet/test -race/coverage + CLI 冒烟
./scripts/verify.sh

# 或逐模块
(cd modules/hpack  && go test -race -cover ./...)
(cd modules/compat && go test -v ./... )   # 黄金语料 + oracle
(cd modules/itest   && go test -v ./...)    # 真实网络往返

# 运行受控服务（h2c，默认 127.0.0.1:8443）
go run ./modules/service/cmd/hpackd serve -c configs/hpackd.json

# 查看 SQLite 中最近解码的请求
go run ./modules/service/cmd/hpackd list -c configs/hpackd.json
```

## 关键正确性属性（每条都有断言）

- **动态表大小更新只在合法位置**：只能出现在头块的第一个头部表示之前；
  之后出现即 `size-update-illegal-position`（核心测试 + x/net 对照）。
- **驱逐严格按字节成本**：每条目 `len(name)+len(value)+32`；缩小上限或插入
  时从最旧条目驱逐，直到 `size <= max`；超过 max 的条目自我驱逐但仍发出。
  RFC 7541 附录 C.5/C.6 精确断言 222/215 字节存活集。
- **索引 0 / 越界拒绝**：`0x80` → `index-zero`；超过静态+动态范围 →
  `index-out-of-range`。
- **Huffman 终止错误拒绝**：未知前缀、流中 EOS、>7 位填充、非全 1 填充一律
  `huffman-invalid`（附录向量 + x/net 同款边界）。
- **连接间状态隔离**：每个连接拥有独立 Encoder/Decoder；一个连接的动态表
  与毒化状态不泄露（核心测试 + 三连接集成测试）。
- **解码失败即失步**：任何 HPACK 错误都会毒化解码器并令服务发送
  GOAWAY `COMPRESSION_ERROR(0x9)` 后关闭连接，绝不继续假设同步。
- **限制解压后头部量**：单字符串、单块总字节、单块字段数三层独立预算。
- **敏感字段不索引**：`Sensitive` 以 never-indexed（`0001` 前缀）发出，编码
  端和解码端动态表都不落该条目；日志中其值以 `<redacted>` 呈现。

## 预期输出如何判断

- `verify.sh` 末尾打印 `ALL CHECKS PASSED`，退出码 0。
- compat 的 `-v` 日志会报告：
  - `decoded 44 stories across 7 independent encoder implementations`
  - `our decoder and x/net agreed on 4198 header blocks ...`
  - `x/net successfully decoded N blocks our encoder produced ...`
- 服务日志为 JSON，含 `conn_id` / `request_id` / `stream_id` 关联，以及
  `step=...`（block-start、dynamic-table-size-update、eviction 等）。
- 失败单独成字段：`error_kind`（稳定机器名）与 `error_detail`，并带
  `fatal: connection-closing; hpack state desynchronized`。

## 依赖版本

| 依赖 | 版本 | 用途 | 是否进生产二进制 |
|---|---|---|---|
| Go | 1.22.2（本机实测） | 工具链 | — |
| modernc.org/sqlite | v1.34.5 | 纯 Go SQLite（审计存储） | 是 |
| golang.org/x/net | v0.33.0 | **仅测试**用独立 HPACK oracle | 否（仅 `_test.go`） |
| http2jp/hpack-test-case | 2019 伪版本 | 公开黄金向量，已 vendor 到 `compat/testdata` | 否（夹具） |

无需生产账号或真实业务数据；所有外部参与者都是本地合成夹具。

## 文档索引

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 模块/包职责与数据流
- [docs/ALGORITHM.md](docs/ALGORITHM.md) — 算法假设与 RFC 条款映射
- [docs/LOGGING.md](docs/LOGGING.md) — 可解释日志字段与失败分类
- [docs/TESTING.md](docs/TESTING.md) — 测试矩阵、夹具来源与独立答案说明
