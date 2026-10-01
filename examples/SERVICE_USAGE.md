# 服务调用示例

以下均为 loopback 受控示例。密钥仅用于测试。

## 1. 启动服务端（IPv4，带完整性 + FINGERPRINT + SQLite 审计）

```bash
$ go run ./cmd/stund -addr 127.0.0.1:3478 -key localstun-test-key \
    -fingerprint -db results/audit.db -run-id demo-run
stund: audit database results/audit.db
stund: listening on 127.0.0.1:3478 (run=demo-run, integrity=true, fingerprint=true)
```

## 2. 客户端发起 Binding

```bash
$ go run ./cmd/stunc -server 127.0.0.1:3478 -key localstun-test-key \
    -fingerprint -count 2
request 1: tx=935cb22a80919965d4f8fe2e xor-mapped=127.0.0.1:57602 rtt=272µs verified=true
request 2: tx=6ba26cc67848665d2e96edc4 xor-mapped=127.0.0.1:57602 rtt=211µs verified=true
```

stderr 同时输出 JSONL 状态事件，例如：

```json
{"run_id":"demo-client","seq":1,"ts":"...","component":"client","event":"request_sent","kind":"","tx_id_hex":"935c...","dst_addr":""}
{"run_id":"demo-client","seq":2,"ts":"...","component":"client","event":"response_matched","kind":"","tx_id_hex":"935c...","src_addr":"127.0.0.1:3478","detail":"matched response after 272µs"}
```

## 3. IPv6

```bash
$ go run ./cmd/stund  -addr '[::1]:3478' -key k -fingerprint -db ''
$ go run ./cmd/stunc  -server '[::1]:3478' -local '[::1]:0' -key k -fingerprint
request 1: tx=09e5e95044c23f563b058c7a xor-mapped=::1:58904 rtt=285µs verified=true
```

## 4. 异常注入（断言结果类别）

```bash
$ go run ./cmd/stuninject -server 127.0.0.1:3478 -mode unknown-required -expect error420
inject mode=unknown-required tx=ac87... bytes=28 -> 127.0.0.1:3478
result bytes=88 outcome=error420 code=420 reason="Unknown Attribute" integrity=true
JUDGMENT MATCH got=error420 want=error420

$ go run ./cmd/stuninject -server 127.0.0.1:3478 -mode tampered -expect dropped
inject mode=tampered tx=0324... bytes=52 -> 127.0.0.1:3478
JUDGMENT MATCH got=dropped want=dropped
```

判定不一致时打印 `JUDGMENT MISMATCH` 并以非零码退出，便于脚本化验收。

## 5. 无完整性的开放模式（对比）

```bash
$ go run ./cmd/stund -addr 127.0.0.1:3480 -key '' -fingerprint=false -db ''
$ go run ./cmd/stunc -server 127.0.0.1:3480 -key '' -fingerprint=false
request 1: tx=... xor-mapped=127.0.0.1:xxxxx rtt=... verified=false
```
