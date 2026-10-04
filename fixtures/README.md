# 夹具说明

最小互操作数据夹具。用途：把"内核解码结果"与"内核之外的独立参考"对拍，
并把二进制格式逐字节锚定。

| 文件 | 来源 | 角色 |
|---|---|---|
| `keys_a.txt` / `keys_b.txt` | 手写（A=100..115，B=108..123） | 输入 |
| `params.json` | 手写（cells=32, k=3, seed=DEFAULT_SEED） | 表形状 |
| `expected_only_a.txt` / `expected_only_b.txt` | `../scripts/gen_expected.sh`（sort/comm） | **独立参考答案**，不经 Rust 内核 |
| `table_a_v1.iblt` / `table_b_v1.iblt` | `cargo run --example gen_fixtures` | 二进制格式契约锚点 |

`tests/format_interop.rs` 做三件事：

1. 解析两个 `.iblt` 夹具，相减解码，结果必须等于 `expected_only_*.txt`；
2. 用 `params.json` 重新编码 `keys_*.txt`，字节必须与 `.iblt` 夹具完全一致；
3. 对夹具做魔数/版本/截断/超限篡改，断言各自的错误类别。

修改键列表或参数后，按 `../README.md` 的"夹具再生成"一节重跑两个生成步骤。
