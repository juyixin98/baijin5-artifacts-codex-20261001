# berd — ASN.1 BER 受限编解码服务

一个以 Go 实现的 ASN.1 BER 编解码服务，支持 INTEGER、BIT STRING、SEQUENCE
与上下文（context-specific）标签，提供带资源上限的字节级解析、DER 规范化
输出、HTTP 受控服务与 SQLite 审计日志。

## 关键约束（设计核心）

1. **资源上限**：长标签（long-form tag）与长长度（long-form length）的解析
   均有字节数上限；嵌套深度、节点总数、输入总大小、INTEGER/BIT STRING
   内容大小同样受限。超限返回 `resource` 类错误并给出精确偏移。
2. **确定/不确定长度边界明确**：BER 模式下不确定长度（indefinite）仅允许
   出现在构造类型上，且可用配置关闭；DER 输出一律为确定长度最短形式。
3. **EOC 语义严格**：EOC（`00 00`）只能终止最内层未闭合的不确定长度构造
   值；出现在定长内容中、顶层或携带非零长度时返回 `eoc` 类错误，绝不当作
   普通零数据吞掉。
4. **位串未使用位校验**：unused-bits 字节必须在 0..7；空位串不允许声明非
   零未使用位；DER 还要求填充位全零。
5. **DER 独立约束**：`VerifyDER` 通过"解析 → 规范化重编码 → 逐字节比对"
   独立判定规范性，报告首个差异偏移。

## 错误模型

所有编解码失败返回分类错误（绝不把异常统一返回成功）：

| category    | 含义 | offset 语义 |
|-------------|------|-------------|
| `truncation`  | 输入提前结束 | 需要更多字节的位置（通常为 len(input)） |
| `syntax`      | 标识/长度/内容字节畸形 | 出错字节位置 |
| `eoc`         | EOC 误用 | EOC 起始位置 |
| `resource`    | 超过配置的资源上限 | 触发上限的结构位置 |
| `indefinite`  | 配置禁用时不确定长度 | 长度字节位置 |
| `constraint`  | DER 规范约束违反 | 首个非规范字节位置 |

## 目录结构

```
cmd/berd/            HTTP 服务入口
cmd/bercli/          离线 CLI（decode/der/verify-der/encode）
configs/berd.json    示例配置（监听地址、SQLite 路径、全部资源上限）
internal/ber/        字节级编解码核心：tag/length/parser(状态机)/value/encode/der
internal/config/     配置加载（JSON 文件 + BERD_* 环境变量覆盖）
internal/server/     HTTP API 与请求流水线（审计、运行身份）
internal/store/      SQLite 审计日志
internal/harness/    测试日志关联设施（run_id、步骤、判定依据）
testcompat/          对照测试：encoding/asn1 与 go-asn1-ber 双参考
internal/ber/testdata/vectors.json  手工编写的 X.690 测试向量
scripts/acceptance.sh  一键验收脚本
```

## 依赖与版本

- Go 1.22（开发环境 go1.22.2 linux/amd64）
- `github.com/mattn/go-sqlite3 v1.14.52`（审计存储，需 cgo/gcc）
- `github.com/go-asn1-ber/asn1-ber v1.5.8`（仅测试依赖，独立参考实现）
- 标准库 `encoding/asn1`（测试参考）、`net/http`、`crypto/sha256`、`crypto/rand`

## 构建与测试

```sh
go build ./...
go test ./...            # 全部单元 + 服务 + 对照测试
go test -v ./...         # 带 run_id/步骤/判定依据的详细日志
go test -cover ./...     # 覆盖率（核心包 ≥80%）
```

## 运行服务

```sh
go run ./cmd/berd -config configs/berd.json
# 或覆盖： BERD_LISTEN=127.0.0.1:9000 BERD_DB_PATH=/tmp/a.db go run ./cmd/berd
```

## 请求样例

```sh
# 健康检查（返回 run_id、版本、Go 版本）
curl -s http://127.0.0.1:8971/v1/health

# 解码 INTEGER 65537
curl -s -X POST http://127.0.0.1:8971/v1/decode -d '{"data":"0203010001"}'

# 解码嵌套不确定长度 SEQUENCE{SEQUENCE{5}}
curl -s -X POST http://127.0.0.1:8971/v1/decode -d '{"data":"3080308002010500000000"}'

# 截断输入 → 422 + {"category":"truncation","offset":2}
curl -s -X POST http://127.0.0.1:8971/v1/decode -d '{"data":"0201"}'

# 游离 EOC → 422 + {"category":"eoc","offset":0}
curl -s -X POST http://127.0.0.1:8971/v1/decode -d '{"data":"0000"}'

# 编码 SEQUENCE{5, -129} 为 DER
curl -s -X POST http://127.0.0.1:8971/v1/encode \
  -d '{"form":"der","spec":{"type":"sequence","children":[{"type":"integer","value":"5"},{"type":"integer","value":"-129"}]}}'

# BER → DER 规范化
curl -s -X POST http://127.0.0.1:8971/v1/canonicalize -d '{"data":"30800201050000"}'

# DER 规范性校验（非规范 → 422 constraint）
curl -s -X POST http://127.0.0.1:8971/v1/verify-der -d '{"data":"0202007f"}'
```

响应统一为 `{ok, result|error, run_id, request_id}`；编解码错误为
HTTP 422，请求格式错误为 400，成功为 200。

## 测试与对照方法

- **手工向量**：`internal/ber/testdata/vectors.json` 依据 X.690 手工编写，
  正例断言解码值，反例断言错误类别与精确偏移，非由被测实现生成。
- **双参考对照**：`testcompat/` 用 `encoding/asn1` 与 `go-asn1-ber` 的输出
  作为参考字节，核验 DER 编码逐字节一致、解码值一致；截断用例要求两个
  库都拒绝。
- **验证案例覆盖**：嵌套不确定长度、超长整数（1025 字节 > 1024 上限）、
  错误 EOC（顶层/定长内/非零长度）、截断（标识/长度/内容/缺 EOC）。
- **日志关联**：测试日志含 run_id、测试名、版本、输入 hex、期望值、实际
  值与判定依据；服务日志为 JSON 行，含 run_id、request_id、输入 SHA-256、
  错误类别/偏移、耗时，并写入 SQLite 审计表。

## 验收复现

从干净目录执行：

```sh
./scripts/acceptance.sh
```

脚本依次：打印环境/依赖版本 → 构建 → 跑全部测试（日志写入
`test-output.log`）→ 以临时配置和数据库启动服务 → 执行上述请求样例 →
打印服务 JSON 日志 → CLI 交叉核验。全部成功才输出
`ACCEPTANCE RESULT`，完整记录写入 `ACCEPTANCE.log`。最近一次执行结果见
[ACCEPTANCE.md](ACCEPTANCE.md)。
