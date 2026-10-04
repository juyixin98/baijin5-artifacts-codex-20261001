# rib — 整数列 RLE + bit-pack 混合块编解码

面向 u64 整数列的混合编码格式与 Axum 服务层。一列被编码为若干首尾相接的
块,每块独立声明模式(BITPACK / RLE)、有效值数与位宽;尾组填充不计入有
效值。格式规范见 [docs/FORMAT.md](docs/FORMAT.md)。

## 工程分层

| 路径 | 职责 |
|------|------|
| `src/format.rs` | 二进制格式:块头布局、模式、位宽合法性规则 |
| `src/bitstream.rs` | LSB-first 位流读写(读侧全程边界检查) |
| `src/encode.rs` | 编码内核:列 → 块序列(模式切换为严格划分,不重不漏) |
| `src/decode.rs` | 解码内核:先验头、再验预算与声明长度,最后才读负载 |
| `src/budget.rs` | 资源控制:值数 / 负载字节 / run 数预算 |
| `src/config.rs` + `config/default.toml` | 配置层(TOML + `RIB_CONFIG` 环境变量) |
| `src/api.rs` + `src/main.rs` | Axum 服务层 |
| `tests/` | 独立测试层:roundtrip / corrupt / golden / api |
| `tests/common/mod.rs` | 合成数据生成器、**独立参考解码器**、结构化测试日志 |

## 依赖与版本

- Rust toolchain:rustc / cargo 1.98.1(2021 edition)
- 运行时依赖(锁定于 `Cargo.lock`):axum 0.8、tokio 1、serde 1、
  serde_json 1、base64 0.22、toml 0.8、tracing 0.1、tracing-subscriber 0.3
- 测试依赖:tower 0.5(util,进程内请求,无需网络)
- 所有输入均为本地合成夹具(xorshift64 确定性生成),无外部账号与真实数据

## 从干净目录复现

```bash
cargo build            # 解析 Cargo.lock,编译
cargo test             # 全部 36 个测试
# 带结构化日志(运行身份 / 输入摘要 / 步骤 / 判定依据):
RIB_TEST_RUN=run-$(date +%Y%m%dT%H%M%S) cargo test -- --nocapture
```

启动服务(配置默认读 `config/default.toml`,可用 `RIB_CONFIG` 覆盖):

```bash
RIB_CONFIG=config/default.toml cargo run --bin rib-server
```

## 请求样例

```bash
curl -s http://127.0.0.1:8080/health
# {"status":"ok"}

curl -s http://127.0.0.1:8080/version
# {"crate":"rib","crate_version":"0.1.0","format_version":1}

curl -s -X POST http://127.0.0.1:8080/v1/encode \
  -H 'content-type: application/json' \
  -d '{"values":[7,7,7,7,7,7,7,7,7,3,1,4,1,5,9,2,6]}'
# {"block_count":2,"input_values":17,"output_bytes":41,"column_b64":"UklCMQEBAwAJAAAABQAAAAkAAAAHUklCMQEABAAIAAAABAAAABMUlWI="}

curl -s -X POST http://127.0.0.1:8080/v1/decode \
  -H 'content-type: application/json' \
  -d '{"column_b64":"UklCMQEBAwAJAAAABQAAAAkAAAAHUklCMQEABAAIAAAABAAAABMUlWI="}'
# {"value_count":17,"values":[7,7,7,7,7,7,7,7,7,3,1,4,1,5,9,2,6]}
```

损坏块头(位宽改为 65)返回 422 与定位信息,不会越界读取:

```json
{"error":{"category":"invalid_bit_width","message":"bit width 65 exceeds maximum 64","offset":6}}
```

预算超限返回 413(`value_count_over_budget` 等)。

## 测试设计

- **roundtrip.rs**:长重复与短变化交替(40 周期模式切换)、位宽跨界
  (2^k−1 / 2^k / 2^k+1,k 至 63,含 u64::MAX)、位宽 0(全零列)、
  非整组尾部(5 个尾值 + 3 个填充)、模式切换边界(恰好 min_run、
  min_run−1)。每个用例同时经内核解码器与 `tests/common` 中的
  **独立参考解码器**(逐位朴素实现,与被测内核不共享代码)解码,
  并与原列逐值对照。
- **golden.rs**:黄金向量为按 FORMAT.md **手工推算**并硬编码的字节
  (含 LSB-first 推导注释),参考答案不由被测内核生成。
- **corrupt.rs**:截断头、坏 magic、非法版本 / 模式 / 位宽(65)、
  保留字节非零、声明长度与公式不符、负载截断、run_len=0、
  run 和不足 / 超出、预算超限——均断言**具体错误类别与字节偏移**。
- **api.rs**:进程内 HTTP 测试,断言具体响应体与状态码类别;
  异常输入一律返回结构化错误,绝不统一返回成功。

测试日志格式:`[run=<id> case=<用例> step=<步骤>] ...`,含 crate 与格式
版本、输入值数与 FNV-1a 摘要、编码字节数、判定依据(verdict basis)。

## 验收记录(如实)

环境:Linux 6.8.0-90-generic,rustc 1.98.1。

- `cargo test`:36 通过 / 0 失败(lib 6、api 5、corrupt 14、golden 3、
  roundtrip 8)。
- 服务冒烟:`/v1/encode` 17 值 → 2 块 41 字节;`/v1/decode` 逐值还原;
  篡改位宽为 65 → HTTP 422 `invalid_bit_width` @ offset 6。
- 已知限制:单块 value_count 上限 u32;超长 run 由编码器拆为连续多块;
  RLE 负载长度不能仅由块头推出,解码器在遍历 run 时校验(见 FORMAT.md)。
