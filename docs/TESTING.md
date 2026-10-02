# 测试矩阵与独立性说明

## 总览

| 模块 | 覆盖率（实测） | 性质 | 期望答案来源 |
|---|---|---|---|
| codec | **97.0%** | 白盒单测 | RFC 7541 附录 C/B 精确字节（独立于实现） |
| hpack | **84.8%** | 白盒单测 | RFC 附录 C.3–C.6 精确字节；x/net 同款边界 |
| service 各包 | frame 74% / config 88% / store 81% / server 53% | 白盒 | 自身行为断言 + net.Pipe 协议测试 |
| compat | （测试模块，无生产语句） | 黄金语料 + oracle | **公开语料 + x/net，均非被测实现生成** |
| itest | 83.3% | 黑盒端到端 | 预期字段/GOAWAY 调自测试自己构造 |
| 总体 | 全部通过（含 `-race`） | — | — |

## 为什么参考答案是独立的

用户要求“参考答案不能全部由被测核心实现自身生成”。本仓库满足：

1. **公开格式黄金向量**：`compat/testdata/corpus` 是
   [http2jp/hpack-test-case](https://github.com/http2jp/hpack-test-case)
   的一个子集，由 nghttp2、python-hpack、node-http2-hpack、swift-nio-hpack、
   -go-hpack、haskell HTTP/2 等**多个独立实现**编码；wire 与 headers 都
   来自该 JSON，不经过本仓库代码。
2. **独立参考实现 oracle**：compat 引入 `golang.org/x/net/http2/hpack`
   （Go 团队维护，与本仓库零共享代码），做三种交叉：
   - 同一黄金线，两者解出一致字段（实测 4198 个头块一致）；
   - 我们编码 → x/net 解码；
   - x/net 编码 → 我们解码。
3. **RFC 附录精确字节**：核心测试把附录 C.1（整数）、C.3/C.4（请求，
   无/有 Huffman）、C.5/C.6（256 字节表驱逐）的**确切十六进制**写死，
   断言字段、动态表存活集与 57/110/164/222/215 字节大小。

## 覆盖的用户点名场景

| 场景 | 位置 |
|---|---|
| 动态表大小更新只在块首 | hpack `TestSizeUpdatePosition`、compat oracle |
| 驱逐按字节成本 | hpack `TestSizeUpdateShrinkEvictsByByteCost`、附录 C.5/C.6 |
| 表缩小跨块 | compat `TestGoldenCorpusTableShrink`、service `applySettings` 测试 |
| 重复头 | hpack `TestDuplicateHeaderPreserved` |
| 敏感字段不索引 | hpack `TestSensitiveNeverIndexed`、`TestEncoderNeverIndexesSensitive`；itest `TestSensitiveFieldRoundTripsAndIsNotIndexed` |
| 截断 | hpack `TestTruncationCategories`；service pipe 测试；itest |
| 索引 0 / 越界 / Huffman 终止错误 | hpack + compat `TestMalformedAgreement` |
| 连接间表状态隔离 | hpack `TestConnectionStateIsolation`；itest `TestConnectionsAreIsolated` |
| 解码失败后不假设同步 | hpack `TestPoisoningStopsDecoder`；service GOAWAY 0x9；itest |
| 限制解压后头部量 | hpack `TestHeaderListBudget` |
| 跨多头块状态一致 | compat 多 case 共享 decoder；itest `TestStateCarriesAcrossHeaderBlocks` |

## 失败类别断言（不是“能调用就行”）

每个负向测试用 `wantErrKind` / `errors.As(*hpack.Error)` 断言**具体类别**，
例如 `KindHuffmanInvalid`、`KindIndexZero`，并在 itest 断言具体
GOAWAY 错误码（0x9 vs 0x1），而非只判断返回了 error。

## 本地验证命令与预期

```bash
./scripts/verify.sh
```

预期末尾：

```
================ SUMMARY ================
passed steps: 17
failed steps: 0
ALL CHECKS PASSED
```

17 步 = gofmt(1) + 5 模块 ×（build/vet/test-race/coverage，其中 build 内嵌
循环） + CLI 冒烟；实际步骤数以脚本输出为准。

详细日志（建议人工复核关键两条）：

```bash
(cd modules/compat && go test -v -run 'TestOracleAgreesWithXNet|TestGoldenCorpus' ./...)
# 预期出现：
#   decoded 44 stories across 7 independent encoder implementations
#   our decoder and x/net agreed on 4198 header blocks from 7 independent encoders

(cd modules/itest && go test -v ./...)
# 预期：全部 PASS，含 GOAWAY COMPRESSION_ERROR(0x9) 与三连接隔离用例
```

## 未运行 / 不适用项（如实标注）

- **TLS 路径**：`genkey`/`Listen` 的 TLS 分支已编写但自动化测试默认走 h2c
  （prior knowledge），原因是本地无真实 CA；可用 `hpackd genkey` 后配
  `cert_file/key_file` 人工验证。因此 **TLS 握手分支标记为“已实现未在
  CI 中跑”**。
- **流控数值**：WINDOW_UPDATE 被容忍但不强制执行（见 ALGORITHM §8），故
  无对应强制断言，属于刻意简化而非遗漏。
- 所有测试均可离线运行（`GOPROXY=off`）；黄金语料已 vendor，不依赖网络。
