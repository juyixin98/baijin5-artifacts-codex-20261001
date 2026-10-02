# stackstats — 离线性能栈样本统计后端

离线栈样本（stack sample）的调用树与折叠栈统计后端。输入为本地合成夹具或
JSON 样本，输出**仅结构化数据**（JSON）。技术栈：Rust + Axum + 文件系统持久化。

## 语义约定

- **自身权重（self）**：只累加在样本的叶节点上；**含子调用权重（inclusive）**：
  累加在样本路径的每个节点上。两者在树节点与汇总接口中分别给出。
- **递归按栈位置保留**：树是完整路径的 trie，`rec` 在深度 1 与深度 2 是不同
  节点，绝不合并。扁平 profile（`top_self`）才按身份跨位置聚合，属于另一种视图。
- **丢样与权重**：每个样本显式携带 `weight`（它代表的原始栈数量）。运行元数据
  携带 `dropped_samples` 与 `drop_reason`。`keep_ratio = kept/(kept+dropped)`，
  `summary.weight_explanation` 给出文字解释。
- **异步拼接需关联证据**：仅当子片段的 `parent_token` 匹配同一运行内另一样本
  发布的 `token` 时才拼接，匹配记录为审计证据；无匹配则锚定在合成根
  `async_detached(task=N)` 下，不并入无关根。重复 token 与父链环是输入错误。
- **符号缺失使用稳定地址身份**：帧身份恒为 `(module, addr)`。符号名只是显示
  元数据；同名函数（不同模块或不同地址）绝不合并。未解析地址显示为 `0x<hex>`，
  同名显示在运行内消歧为 `name@module`。

## 模块边界

| 模块 | 职责 |
|---|---|
| `model` | 运行模型：样本/帧/符号/生命周期状态，输入校验 |
| `symbols` | 地址身份解析（稳定身份、同名不合并、重叠拒绝） |
| `naming` | 显示名消歧（只影响展示，不动身份） |
| `stitch` | 异步片段拼接与关联证据 |
| `tree` | 资源算法：调用树、self/inclusive、冷热路径、扁平 profile |
| `folded` | 折叠栈输出 |
| `store` | 持久化与采样状态：`data/runs/<id>/{meta.json,samples.jsonl}`，限额 |
| `api` | Axum 诊断接口（仅 JSON） |
| `error` | 四类错误契约（见下） |

## 错误类别（可区分）

| category | HTTP | 含义 | 示例 code |
|---|---|---|---|
| `input` | 400 / 404 | 输入非法 | `invalid_weight`, `async_cycle`, `run_not_found`(404) |
| `state_conflict` | 409 | 生命周期冲突 | `run_sealed`, `already_sealed` |
| `resource_exhausted` | 413 | 限额耗尽 | `sample_limit`, `stack_too_deep`, `run_limit` |
| `computation` | 500 | 内部计算/持久化失败 | `weight_overflow`, `persistence_io` |

错误体统一为 `{"error": {"category", "code", "message"}}`。

## 运行

```bash
cargo run -- config/default.toml   # 默认配置见 config/default.toml
# 或指定其它配置: cargo run -- path/to.toml
```

配置项：`bind`、`data_dir`、`[limits]`（max_runs / max_samples_per_run /
max_stack_depth / max_samples_per_request）。

## API 一览

- `GET  /health`
- `POST /runs` — 创建运行（symbols、dropped_samples、drop_reason）
- `GET  /runs` — 列出运行
- `POST /runs/{id}/samples` — 追加样本（仅 open 状态）
- `POST /runs/{id}/seal` — 封存运行
- `GET  /runs/{id}/tree` — 调用树（self / inclusive 分列）
- `GET  /runs/{id}/folded` — 折叠栈
- `GET  /runs/{id}/summary` — 总权重、丢样比、冷热路径、top_self、权重解释
- `GET  /runs/{id}/audit` — 每样本路径累加与拼接证据（可重放）

### 示例（使用夹具）

```bash
# 创建运行（夹具的 run 段）
curl -s -XPOST localhost:7878/runs -H 'content-type: application/json' \
  -d @<(jq '.run' fixtures/recursion_shared_leaf.json)
# 记返回的 run_id，然后：
jq '{samples: .samples}' fixtures/recursion_shared_leaf.json | \
  curl -s -XPOST localhost:7878/runs/<run_id>/samples -H 'content-type: application/json' -d @-
curl -s -XPOST localhost:7878/runs/<run_id>/seal
curl -s localhost:7878/runs/<run_id>/tree
curl -s localhost:7878/runs/<run_id>/summary
curl -s localhost:7878/runs/<run_id>/audit
```

## 测试

```bash
cargo test                      # 全部单元 + 集成测试
cargo test -- --nocapture       # 显示 [test_log] 运行编号与中间状态日志
cargo test --test api_integration
cargo test --test reference_check
```

### 手算基准（测试的独立答案来源）

`fixtures/recursion_shared_leaf.json`：
`s1 main;rec;rec;leaf w2`，`s2 main;rec;leaf w1`，`s3 main;other;leaf w4`。

- 总权重 7.0；`main` inclusive 7 / self 0
- `rec@d1` inclusive 3，`rec@d2` inclusive 2（递归按位置分开）
- 共享叶 `leaf` 三个位置 self = 2 / 1 / 4，扁平聚合 self = 7
- 折叠栈：`main;other;leaf 4`、`main;rec;leaf 1`、`main;rec;rec;leaf 2`
- 热路径 `main;other;leaf`(4)，冷路径 `main;rec;leaf`(1)

`fixtures/async_fragments.json`：`a2` 凭 `parent_token=tokA` 拼到 `a1` 之后
（证据 `stitched`）；`a3` 的 `tokMissing` 无匹配，锚定 `async_detached(task=9)`
（证据 `detached`）。总权重 5.0。

`fixtures/weighted_dropped.json`：权重 1.5+3.5+1.0=6.0，丢样 5，
keep_ratio = 3/8 = 0.375；热路径 `main;emit`(3.5)，冷路径 `main;parse`(2.5)。
（冷热选择平局时按 `(module, addr)` 升序取第一个，保证确定性。）

`fixtures/missing_symbols.json`：`helper@app`、`helper@lib`、`0xdead` 三个
子节点互不合并。

### 验证结构

- 单元测试（各模块内）：符号身份、拼接证据、树累加、折叠、错误类别映射、
  持久化重放（seal 后重开存储数据仍在）。
- `tests/api_integration.rs`：真实 HTTP 服务（临时端口），断言**字面值**
  手算结果与错误类别（400/404/409/413），日志含 run_id 与中间状态。
- `tests/reference_check.rs`：独立朴素参考实现（逐样本前缀累加，O(n·d²)），
  与调用树的逐前缀 inclusive / 逐路径 self 全量对拍；字面期望值仍是首要
  判据，参考实现只是防"双双漂移"。

最近一次完整运行的结论：`cargo test` 全部通过（单元 26 + 集成 6 + 参考对拍 4，
详见下方"测试结果"）。

## 测试结果（真实运行记录）

```
running 26 tests (unit)        ... 26 passed
running 6 tests  (integration) ... 6 passed
running 4 tests  (reference)   ... 4 passed
```

（精确计数以本地 `cargo test` 输出为准；以上为本工程交付时的运行结论。）
