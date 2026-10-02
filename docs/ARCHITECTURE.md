# 架构与模块关系

## 总览

```
                         ┌──────────────────────────────────────┐
                         │             itest (黑盒)              │
                         │  真实 h2c TCP：隔离/截断/敏感/缩小/落库 │
                         └───────────────┬──────────────────────┘
                                         │ 依赖（仅测试）
                         ┌───────────────▼──────────────────────┐
                         │              service                  │
   ┌──────────┐  h2c/TLS │  cmd/hpackd  config  frame  store     │
   │  合成客户端 │────────▶│  server: 每连接 Encoder/Decoder       │
   └──────────┘          └───────────────┬──────────────────────┘
                                         │
                         ┌───────────────▼──────────────────────┐
                         │               hpack                   │
                         │  静态表 / 动态表 / 状态机 / 观察者      │
                         └───────────────┬──────────────────────┘
                                         │
                         ┌───────────────▼──────────────────────┐
                         │               codec                   │
                         │  前缀整数 / 字符串 / 静态 Huffman      │
                         └──────────────────────────────────────┘

  compat（仅测试）同时链接 hpack 与 golang.org/x/net/http2/hpack，
  并用 vendor 到 testdata 的 http2jp/hpack-test-case 公开黄金语料。
```

## 模块职责

### `modules/codec` — 字节编解码

无任何“表/连接”语义的原语层：

- `integer.go` — RFC 7541 §5.1 的 1..8 位前缀整数。解码以 `2^62-1` 为硬上界，
  续接字节缺失返回 `ErrTruncatedInteger`，超界返回 `ErrIntegerOverflow`。
- `huffman.go` — RFC 7541 附录 B 的静态 Huffman 码。码长是规范性常量
  （256 项，直接取自 RFC），码字在 `init` 中用规范前缀码算法**推导**而非
  硬编码，compat 模块会与 x/net 的码字做全量交叉验证。
- `string.go` — §5.2 字符串：H 位、长度、明文或 Huffman；长度上限在
  **解压后**判定。

### `modules/hpack` — 协议状态机

- `static.go` — 附录 A 的 61 条静态表。
- `dynamic.go` — 动态表。`ents[0]` 为最旧；协议索引最新在前。插入即驱逐，
  字节成本 `len(name)+len(value)+32`。
- `decoder.go` / `representations.go` — 四种表示（§6.1/6.2.1/6.2.2/6.2.3）
  与表大小更新（§6.3）的块级解析、三层预算、伪首部校验、**毒化**与观察者。
- `encoder.go` — 编码端，独立维护动态表，敏感字段强制 never-indexed，
  SETTINGS 变更排队为下一块**开头**的大小更新。
- `errors.go` — 单一 `Error` 类型 + 稳定的 `Kind` 分类，测试与日志据此断言，
  不匹配消息文本。

### `modules/service` — 受控 HTTP/2 服务

- `frame/` — 手写的最小 RFC 7540 帧层（读写 9 字节帧头、SETTINGS/HEADERS/
  CONTINUATION/DATA/GOAWAY/PING）。
- `server/` — 每连接一个 goroutine，拥有**独立**的 `hpack.Decoder/Encoder`；
  汇聚 HEADERS+CONTINUATION 为完整块后调用 `DecodeBlock`；失败即
  GOAWAY(`COMPRESSION_ERROR`) 并关闭。
- `store/` — modernc.org/sqlite（纯 Go，无 CGO）。`requests` 与 `headers`
  两张表，按 `conn_id`/`request_id`/`stream_id` 关联。
- `config/` — JSON 配置（刻意只用标准库，减少离线依赖）。
- `cmd/hpackd` — `serve` / `list` / `genkey` 三个子命令。

### `modules/compat` — 兼容性（独立答案）

- 解码 7 个独立实现（nghttp2、python-hpack、node-http2-hpack、swift-nio、
  go-hpack 等）产出的公开黄金向量，跨多 case 共享压缩上下文。
- 用 `golang.org/x/net/http2/hpack` 作 **oracle**：同一黄金线，我们与 x/net
  必须解出完全一致的字段序列；同时双向交叉（我们编码→x/net 解码；x/net
  编码→我们解码）。

### `modules/itest` — 独立集成测试

黑盒：in-process 启动真实服务，通过 `h2t`（一个只依赖标准库 + 本仓库 frame
包的最小 h2c 客户端）走真实 TCP/TLS-less 回环，断言跨块状态、连接隔离、
GOAWAY 错误码、敏感字段与 SQLite 落库。

## 关键不变量

1. HPACK 状态严格 per-connection，无任何包级/全局可变表。
2. `frame.Writer` 的帧头缓冲是栈上数组，允许同进程客户端/服务端并发。
3. 解码失败后该连接不再接受任何块；上层必须关连接（RFC 7541 将压缩错误
   定义为连接级错误）。
4. 方向语义：对端的 `SETTINGS_HEADER_TABLE_SIZE` 只收缩**我方编码器**的表；
   我方解码器的上限由我方自己通告的值固定。
