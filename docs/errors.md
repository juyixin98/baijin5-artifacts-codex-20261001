# 错误类别、偏移语义与状态映射

所有失败都返回稳定的机器可读类别 `error.category`、人类可读 `message`、
**精确字节偏移** `error.offset` 与所在深度 `error.depth`。

## 偏移语义（契约）

| 场景 | offset 指向 |
| --- | --- |
| 截断（缺标识/长度/内容/EOC 被截断） | 本应出现下一个字节的位置，即 `len(input)` |
| 非法八比特 | 该八比特的下标 |
| 非法长度字段 | 长度字段首字节下标 |
| 错位 EOC | EOC 起始 `0x00` 的下标 |
| 顶层值之后多余数据 | 第一个多余字节下标；若多余字节是 `00 00`，报 `MALFORMED_EOC` |
| DER 非规范长度/标签 | 对应字段首字节下标 |
| 内容规则（空 INTEGER、未使用位） | 内容起始（头部结束）下标；尾部比特违例指向最后一个内容字节 |

## 类别目录

| category | 含义 | HTTP |
| --- | --- | --- |
| `TRUNCATED` | 输入在 TLV 完成前结束（含不确定长度缺 EOC、半截 EOC） | 400 |
| `INVALID_TAG` | 非法/保留标识八比特（如 universal 标签 0） | 400 |
| `INVALID_LENGTH` | 非法长度形式（`0xFF`、原始值不确定长度） | 400 |
| `LENGTH_OVERFLOW` | 长度的长度超过 `max_length_bytes` | 413 |
| `SIZE_EXCEEDED` | 超过内容/标签号/整数宽度/子元素数上限 | 413 |
| `DEPTH_EXCEEDED` | 嵌套深度超过 `max_depth` | 413 |
| `MALFORMED_EOC` | EOC 没有匹配的不确定长度构造（顶层/确定长度内/多余 EOC/孤立 `00`） | 400 |
| `INVALID_BIT_STRING` | 未使用位计数非法、空串非 0、尾部低比特非 0 | 400 |
| `INVALID_INTEGER` | INTEGER 内容为空等 | 400 |
| `INVALID_ENCODING` | TLV 结构完好但违反类型/DER 规则（构造性、最小形式、BOOLEAN、SET 序等） | 400 |
| `TRAILING_DATA` | 单个顶层值之后存在普通多余字节 | 400 |
| `UNSUPPORTED` | 超出受限 profile（APPLICATION/PRIVATE 类、范围外通用标签） | 422 |
| `ENCODE_ERROR` | 编码入参非法 | 422 |
| `INVALID_REQUEST` / `INVALID_HEX` | 请求不是合法 JSON / 十六进制 | 400 |
| `REQUEST_TOO_LARGE` |  HTTP 请求体超过 `max_body_bytes` | 413 |
| `INTERNAL` / `INTERNAL_INCONSISTENCY` / `UNHEALTHY` | 内部异常、编码器自检失败、存储不可用 | 500/503 |

资源类拒绝用 413（请求超过部署 profile），结构类错误用 400，
结构完好但超出 profile 用 422。

## 被测核心与成熟库的已知分歧

`go-asn1-ber` v1.5.8 是通用 BER 解码器，下列输入它接受（或宽松处理），
本服务依据受限 profile / DER / 单值信封拒绝；分歧在
`test/oracle/compat_test.go` 的 `rejectedBySUT` 表中逐条登记：

- 顶层裸 `00 00`、确定长度构造内的 `00 00`、闭合后多余的 `00 00`
  （库把 `00 00` 当 EOC 包或留在剩余字节中）；
- 顶层单值之后的尾随字节（库只解第一个 TLV）；
- 空 INTEGER、未使用位非法的 BIT STRING（库不做内容语义校验）；
- DER 才禁止的：不确定长度、非最小长度/整数（库为 BER 解码）。

任何未登记的分歧都会让 `TestOracleVerdictsOnRejectedInputs` 失败。
