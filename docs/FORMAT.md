# RIB-1 二进制块格式规范

RIB(RLE / Int-pack Block)是面向整数列(u64 逻辑值)的混合编码格式。
一列被编码为**一个或多个首尾相接的块**,每个块独立声明自己的模式、
值数与位宽。所有多字节整数均为小端序。

## 块头(固定 16 字节)

| 偏移 | 长度 | 字段 | 说明 |
|------|------|------|------|
| 0    | 4    | magic       | 常量 `RIB1`(0x52 0x49 0x42 0x31) |
| 4    | 1    | version     | 必须为 1 |
| 5    | 1    | mode        | 0 = BITPACK,1 = RLE |
| 6    | 1    | bit_width   | 合法区间 **[0, 64]**,含义见下 |
| 7    | 1    | reserved    | 必须为 0 |
| 8    | 4    | value_count | 本块**有效值**个数(u32) |
| 12   | 4    | payload_len | 紧随块头的负载字节数(u32) |

## 位宽合法性(固定规则)

- `bit_width ∈ [0, 64]`,越界即块头损坏。
- `bit_width = 0` 表示本块所有值均为 0,负载中不存储任何值位。
- `bit_width = 64` 合法,表示值按完整 u64 存储。
- 编码器只允许在块内最大值实际需要的位宽下声明 `bit_width`
  (`bit_width = 64 - leading_zeros(max)`,`max = 0` 时为 0)。

## BITPACK 负载(mode = 0)

- 值按 **8 个一组**打包,每组占 `bit_width` 字节
  (8 值 × bit_width 位 = bit_width 字节)。
- 组内值按 **LSB-first**(值的第 0 位写入流的第 0 位)紧凑排列。
- `payload_len = ceil(value_count / 8) * bit_width`。
- 当 `value_count % 8 != 0` 时,最后一组用 0 值**填充**;
  填充位**不是有效值**,解码器只产出前 `value_count` 个值。
- `bit_width = 0` 时 `payload_len` 必须为 0。

## RLE 负载(mode = 1)

- 负载为若干 run 的串联,每个 run:
  - `run_len`:u32 小端,必须 ≥ 1;
  - `value`:`ceil(bit_width / 8)` 字节小端(`bit_width = 0` 时为 0 字节)。
- 所有 run 的 `run_len` 之和必须恰好等于 `value_count`,
  且所有 run 字节之和必须恰好等于 `payload_len`。

## 解码前置校验(资源控制)

解码器在读取任何负载字节**之前**必须:

1. 校验 magic / version / mode / reserved / bit_width;
2. 按调用方给定的预算校验 `value_count` 与 `payload_len` 上限;
3. 按上表公式重算期望负载长度,与声明的 `payload_len` 比对;
4. 校验缓冲区实际剩余字节 ≥ `payload_len`。

任一校验失败都必须以**带字节偏移与字段名的结构化错误**终止,
不允许越界读取或静默截断。
