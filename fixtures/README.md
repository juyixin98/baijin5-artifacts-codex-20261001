# Fixtures

最小合成夹具，全部为本地数据，无外部账号或真实业务数据。

## 文件约定

- 首行为**类型化表头**：`列名:类型,...`
- 支持类型：`bigint`(i64)、`double`(f64)、`boolean`、`text`(UTF-8)
- 引号遵循 RFC 4180：`"a,b"`、`"he said ""hi"""`
- NULL 拼写：**未加引号的空字段** 或字面量 `\N`
- 加引号的空字段 `""` 是空字符串，**不是** NULL
- 读取时按 `batch_rows` 切块为多个 Arrow2 `Chunk`，保证算子真实处理多批次

## 夹具清单

| 文件 | 用途 |
|------|------|
| `edge_left.csv` / `edge_right.csv` | 两列 text：嵌套 NULL（`(x,NULL)`、`(NULL,y)`、`(NULL,NULL)`）、边界易碰撞对 `(12,3)` vs `(1,23)` vs `("1","23")`；右侧 `(NULL,NULL)` 重复两次，用于 ALL 计数 |
| `typed_left.csv` / `typed_right.csv` | 五列混合类型：重复行、倾斜键（grp=0）、含逗号/引号的字符串、UTF-8、各种 NULL 位置 |

## 生成大规模夹具

```bash
cargo run -- fixture --out fixtures/generated --rows 20000 --keyspace 200 --kind all
```

生成器使用固定种子的 xorshift，输出可复现；含约 25–30% 倾斜键（grp=0）、
重复标签、NULL 与易碰撞字符串。
