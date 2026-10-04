# rbp-column — 整数列 RLE / bit-pack 混合块格式

u64 整数列的混合 RLE / bit-pack 块编解码内核，附带 Axum 互操作服务。
所有数据均为本地合成夹具，无外部账号或真实业务数据依赖。

## 二进制格式（RBP1，版本 1）

全部整数小端。

```
流:    magic "RBP1" (4 字节) + 块序列直至 EOF

块头:  8 字节
       u8  mode         0 = RLE, 1 = BITPACK
       u8  bit_width    0..=64（0 与 64 均合法，合法性固定）
       u16 reserved     必须为 0
       u32 value_count  本块有效值个数

块体:  RLE:      ceil(bit_width/8) 字节，被重复的值（bit_width=0 时为 0 字节，值隐含为 0）
       BITPACK:  ceil(value_count/8) * bit_width 字节
                 值按 LSB-first 打包，每 8 个值一组；
                 尾组填充槽不计入 value_count，解码时忽略
```

解码器在解码任何值之前：校验头合法性（mode / reserved / bit_width），
用检查算术计算声明体长并对照剩余输入验证，再套用资源预算
（块数 / 值数 / 字节数）。损坏的块头产生**带定位的错误**
（错误类别 + 块序号 + 字节偏移），绝不越界读取。

## 工程结构

```
src/format.rs   二进制格式定义（块头、位打包原语）
src/encode.rs   编码内核：先做最大等值游程分段，再选模式 —— 模式切换不丢边界值
src/decode.rs   解码内核：解码前按声明预算验证长度，错误全部带定位
src/error.rs    错误分类（ErrorCategory，API 与测试断言共用）
src/config.rs   配置层（TOML + 环境变量覆盖）
src/server.rs   Axum 服务（/v1/encode, /v1/decode, /version, /healthz）
config/default.toml  服务与预算配置
tools/gen_fixtures.py  独立 Python 实现，生成参考夹具（参考答案不来自 Rust 核心）
fixtures/manifest.json 黄金测试向量（9 个用例）
tests/          roundtrip / corruption / golden / api 四层测试 + common 测试日志层
```

## 环境依赖（验收时实测版本）

- Rust 工具链：rustc 1.98.1 / cargo 1.98.1（edition 2021）
- Python 3.12（仅用于重新生成夹具，非必需）
- 主要依赖（Cargo.lock 锁定）：axum 0.8.9, tokio 1.53.2, serde 1.0.229,
  serde_json 1.0.151, thiserror 2.0.21, tracing 0.1.44,
  tracing-subscriber 0.3.23, tower-http 0.6.11, hex 0.4.3, toml 0.8.23,
  reqwest 0.12.28（仅测试）

## 从干净目录复现

```bash
# 1. 构建
cargo build

# 2. 全部测试（25 个：roundtrip 6 / corruption 11 / golden 2 / api 6）
cargo test

# 3. 查看结构化测试日志（运行身份 + 输入指纹 + 判定依据）
cargo test -- --nocapture 2>&1 | grep '\[rbp-test\]'

# 4. 启动服务（配置见 config/default.toml；RBP_CONFIG/RBP_HOST/RBP_PORT 可覆盖）
cargo run

# 5. （可选）重新生成参考夹具
python3 tools/gen_fixtures.py
```

## 请求样例

```bash
# 版本信息
curl -s localhost:8080/version
# {"crate_version":"0.1.0","format_version":1,"rustc":"rustc 1.98.1 ..."}

# 编码：8 个 7（RLE）+ 3 个字面量（bit-pack）+ 8 个 255（RLE）
curl -s -X POST localhost:8080/v1/encode -H 'content-type: application/json' \
  -d '{"values":[7,7,7,7,7,7,7,7,1,2,3,255,255,255,255,255,255,255,255]}'
# {"run_id":"boot-...-req-000000","hex":"52425031...","stats":{"blocks":3,...}}

# 解码（用上一步返回的 hex）
curl -s -X POST localhost:8080/v1/decode -H 'content-type: application/json' \
  -d '{"hex":"52425031000300000800000007010200000300000039000008000008000000ff"}'
# {"run_id":"...","values":[7,7,7,7,7,7,7,7,1,2,3,255,...],"stats":{...}}

# 损坏块头：mode=9 非法 → 422 + 定位信息（不是 200）
curl -s -X POST localhost:8080/v1/decode -H 'content-type: application/json' \
  -d '{"hex":"524250310900000000000000"}'
# HTTP 422 {"error":{"category":"invalid_mode","block_index":0,"offset":4,...}}
```

## 配置（config/default.toml）

| 键 | 默认 | 含义 |
|---|---|---|
| server.host / server.port | 127.0.0.1 / 8080 | 监听地址 |
| encoder.rle_min_run | 8 | 成为 RLE 块的最小游程长度 |
| budget.max_blocks | 65536 | 单流最大块数 |
| budget.max_values | 16777216 | 单流最大解码值数 |
| budget.max_bytes | 67108864 | 单流最大字节数 |
| max_http_body_bytes | 4194304 | HTTP 请求体上限 |
| max_encode_values | 4194304 | 单次 encode 值数上限 |

## 测试日志约定

每个用例输出可关联运行与输入的结构化行：

```
[rbp-test][run=<pid>-<毫秒时间戳>] case=<用例> crate=0.1.0 format=1 event=begin
[rbp-test][run=...] case=... step=1 input_values=180 fingerprint=9c3e... 
[rbp-test][run=...] case=... verdict=PASS basis="per-value equality vs independent Python fixture"
```

判定依据（basis）写明通过原因：逐值对照、字节级对照、具体错误类别与定位等。
失败即测试失败，不存在“异常统一返回成功”的路径：服务端未知错误映射为
500/internal，可识别错误映射为 4xx + 具体 category。

## 验收记录（2026-10-04，本仓库实际执行）

- `cargo build`：通过，0 警告。
- `cargo clippy --all-targets`：0 警告（曾修复测试代码 8 处风格 lint）。
- `cargo test`：25/25 通过
  （api 6、corruption 11、golden 2、roundtrip 6）。
- `cargo test -- --nocapture`：结构化日志 266 行，含 run id、输入指纹、
  逐步骤进度与判定依据。
- 冒烟：`cargo run` 后 curl 实测 /version、/healthz、/v1/encode、
  /v1/decode 正常；非法 mode 块头返回 422 `invalid_mode`（block_index=0,
  offset=4）；空列编码返回 422 `empty_input`。
- 测试期间发现并修复 1 个真实缺陷：`EncodeStats.output_bytes` 曾重复计入
  4 字节 magic（36 vs 实际 32），已修复并加入回归断言
  （tests/api.rs 中 output_bytes == hex 长度一半）。
- 干净目录复现：`git clone` 到临时目录后 `cargo test` 全绿（见下文提交记录）。
