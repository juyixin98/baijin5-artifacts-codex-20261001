# ber-restricted-codec

一个**受限（resource-bounded）的 ASN.1 BER/DER 编解码服务**。按字节实现 TLV 编解码与
协议状态机，以受控 HTTP 服务暴露，用 SQLite 落审计日志；测试使用本地合成夹具并与成熟
ASN.1 库 `go-asn1-ber` 做独立对照。

支持的类型范围（明确边界）：

- `INTEGER`（任意精度，宽度受 `max_integer_bytes` 约束）
- `BIT STRING`（原始/构造形式；强制未使用位校验）
- `SEQUENCE` / `SET`（`SET` 在 DER 模式下强制编码序排序）
- 上下文标签 `[n]`（构造/原始）
- 附带支持 `BOOLEAN`、`NULL`、`OCTET STRING`（其中 DER 形式约束独立执行）

明确拒绝：`APPLICATION` / `PRIVATE` 类、范围外的通用标签、原始值不确定长度、
保留长度字节 `0xFF`、无匹配构造的 EOC、DER 下的非规范编码。

## 关键约束如何落实

| 约束 | 实现位置 |
| --- | --- |
| 长标签/长长度解析有资源上限 | `internal/ber/decoder.go`：`max_tag_bytes`、`max_tag_number`、`max_length_bytes` |
| 内容跨度、嵌套深度、子元素数、整数宽度有上限 | `Limits` + `parseChildren`/`validateInteger` |
| 确定长度与不确定长度边界明确 | 原始值只允许确定长度；构造值允许 `0x80` 不确定长度且必须由 EOC 闭合 |
| EOC 只能终止匹配的构造值，不能当零数据吞掉 | `leadingEOC` + `MALFORMED_EOC`（顶层/确定长度内/多余 EOC 均拒绝并给精确偏移） |
| 位串未使用位校验 | `validate.go:checkBitStringPayload`（计数 0..7、空串必须为 0、尾部低比特必须为 0） |
| DER 规范输出独立约束 | `ValidateDER`：确定长度、最小长度/标签、最小整数、BOOLEAN 00/FF、原始位串、SET 排序 |
| 不把异常/未知状态当成功 | HTTP 统一信封 `success=false` + `category` + `offset`；panic 恢复返回 500 |
| 日志关联输入/运行身份 | 每行带 `run_id`、`input_sha256`、`input_len`；debug 级输出逐步偏移与判定依据（X.690 条款） |

## 目录结构

```
cmd/berserv/          服务入口
cmd/genfixtures/      夹具生成器（手工向量 + go-asn1-ber 向量，两源独立）
internal/ber/         受限编解码核心（types/decoder/encoder/validate/bigint）
internal/config/      JSON + 环境变量配置层
internal/logx/        结构化日志、run id、输入指纹、解析追踪
internal/store/       SQLite 审计存储（modernc 纯 Go 驱动默认，mattn cgo 可选）
internal/server/      HTTP 协议状态机、统一信封、中间件
internal/oracle/      成熟库 go-asn1-ber 的独立预言机封装
test/oracle/          对照/差异/变异测试
test/integration/     服务端到端测试（httptest + 内存 SQLite）
configs/              配置样例
fixtures/             生成的合成夹具 vectors.json
scripts/              一键构建/测试/运行脚本
docs/                 协议与错误目录文档
```

## 依赖版本

- Go **1.22.2**（`go.mod` 声明 `go 1.22.2`）
- `modernc.org/sqlite` **v1.34.5**（纯 Go，默认，无需 cgo）
- `github.com/go-asn1-ber/asn1-ber` **v1.5.8**（仅用于夹具生成与独立对照，不进入服务运行路径）
- 可选 `github.com/mattn/go-sqlite3` **v1.14.52**（cgo，构建标签 `cgo`）
- 其余仅标准库；`go.sum` 锁定全部传递依赖

## 从干净目录复现

```bash
# 1. 生成夹具（联网拉一次模块；之后测试离线可跑）
go run ./cmd/genfixtures -out fixtures

# 2. 构建
go build ./...

# 3. 全量测试（含 -race 与覆盖率）
go test -race ./...
go test -cover ./...

# 4. 启动服务（默认 127.0.0.1:8480，文件 SQLite 落 data/berserv.db）
go run ./cmd/berserv -config configs/config.json
```

也可以用脚本：`scripts/check.sh`（生成夹具 + vet + race 测试 + 覆盖率）。

## 请求样例

```bash
# 解码（BER）
curl -s 127.0.0.1:8480/v1/decode \
  -d '{"input_hex":"a080308002010500000000","mode":"BER","trace":true}'

# DER 校验（同一编码若含不确定长度将失败并给偏移）
curl -s 127.0.0.1:8480/v1/validate \
  -d '{"input_hex":"30800000","mode":"DER"}'

# 编码（确定长度 DER / BER / BER_INDEFINITE）
curl -s 127.0.0.1:8480/v1/encode -d '{
  "encoding":"BER_INDEFINITE",
  "node":{"class":"universal","tag":16,"constructed":true,
    "children":[{"class":"universal","tag":2,"constructed":false,"value_hex":"05"}]}}'
```

成功响应：

```json
{
  "success": true,
  "data": {"node": {"class":"universal","tag":16,"constructed":true,
    "start_offset":0,"end_offset":10,"children":[ ... ]}},
  "meta": {"run_id":"...","duration_us":42,"version":"1.0.0",
           "decision_basis":"X.690 clause 8: BER, ..."}
}
```

失败响应（**永不**返回成功）：

```json
{
  "success": false,
  "error": {"category":"MALFORMED_EOC","message":"...","offset":2,"depth":1},
  "meta": {"run_id":"...","duration_us":11,"version":"1.0.0"}
}
```

错误类别、HTTP 状态映射与偏移语义见 `docs/errors.md`。

## 配置

`configs/config.json` 为默认样例；环境变量以 `BERSERV_` 前缀覆盖
（如 `BERSERV_MAX_INTEGER_BYTES=256 BERSERV_LISTEN_ADDR=127.0.0.1:9000`）。
零值或缺项自动回填内置默认 profile；表名做安全标识符校验。

## 测试组织与独立性声明

- `internal/ber/*_test.go`：按字节的表驱动单测，断言**具体值、失败类别与精确偏移**，
  而非"接口能调用"。
- `test/oracle/compat_test.go`：对每个夹具，被测核心与 `go-asn1-ber` 分别解码；
  合法输入比对整棵值树（含 301 字节超长整数的原值），非法输入断言被测类别/偏移并
  **显式登记库间分歧**（BER 允许但受限 profile/DER 禁止的集合）。
- `test/oracle/mutation_test.go`：单字节变异差异测试，保证"成熟库拒绝的被测绝不接受"，
  且被测更严格时类别必须属于已登记的 profile 类别。
- 参考答案由 `cmd/genfixtures` 从两个独立来源生成（手工推导 + 成熟库），
  **不**由被测核心自身生成；夹具签入仓库。

## 三方交叉验证（可选，需要 Python）

除 Go 的 `go-asn1-ber` 对照外，还可用另一种语言的成熟实现 pyasn1 做三角验证：

```bash
python3 scripts/crosscheck_pyasn1.py          # 已在 pyasn1 0.4.8 验证
```

该脚本对全部 35 个夹具断言：合法输入 pyasn1 也能无剩余解码、所有 INTEGER 原值
（含 301 字节超长整数）一致，纯 universal 树逐节点一致；非法输入逐条打印
pyasn1 的判定，使 BER 宽松性与受限 profile/DER 之间的分歧可见。pyasn1 对无
schema 的原始上下文标签无法表示，这一点在脚本中显式登记为库限制。
