# 服务调用示例（本地 TCP，行分隔 JSON）

协议：每个 TCP 连接写入**一行** JSON（`BatchRequest`），半关闭写端后服务端
返回一行 JSON。无外部账号、无网络依赖（仅本机回环）。

## 1. 启动

```bash
cargo run -- serve --addr 127.0.0.1:8140
```

## 2. 正常：交替量词（路径引用本地夹具）

请求：

```json
{"model_path":"fixtures/model/finite_model.json","formula_path":"fixtures/formulas/f03_alt_true.json","run_id":"srv-ok","allow_empty_domain":false}
```

调用：

```bash
echo '{"model_path":"fixtures/model/finite_model.json","formula_path":"fixtures/formulas/f03_alt_true.json","run_id":"srv-ok"}' \
  | cargo run --example tcp_client -- --addr 127.0.0.1:8140
```

响应（节选）：

```json
{
  "run_id": "srv-ok",
  "verdict": "false",
  "instantiations_used": 9,
  "residual_quantifiers": 0,
  "check_report": { "ok": true, "reference_truth": false }
}
```

## 3. 预算不足：保留量词并标 unknown

```bash
echo '{"model_path":"fixtures/model/finite_model.json","formula_path":"fixtures/formulas/f10_budget_mid.json","run_id":"srv-budget","budget":4}' \
  | cargo run --example tcp_client -- --addr 127.0.0.1:8140
```

响应（节选）：

```json
{ "verdict": "unknown", "instantiations_used": 0, "residual_quantifiers": 1,
  "reason": "budget 4 exhausted ... 1 quantifier(s) retained" }
```

## 4. 异常：输入错误（未知谓词）

```bash
echo '{"model_path":"fixtures/model/finite_model.json","run_id":"srv-bad","formula":{"op":"pred","name":"ghost","args":[]}}' \
  | cargo run --example tcp_client -- --addr 127.0.0.1:8140
```

```json
{"run_id":"srv-bad","error":{"kind":"input","code":"unknown_symbol","message":"unknown predicate ghost"}}
```

## 5. 异常：非法 JSON

输入 `{not json` →

```json
{"run_id":"unknown","error":{"kind":"input","code":"fixture_invalid_json","message":"..."}}
```

## 6. 内联模型与公式（无需文件）

```json
{
  "model": {
    "sorts": [{"name":"T","elements":["a","b"]}],
    "constants": [], "functions": [],
    "predicates": [{"name":"p","param_sorts":["T"],"facts":[["a"]]}]
  },
  "formula": {"op":"forall","var":"t","sort":"T",
    "inner":{"op":"pred","name":"p","args":[{"t":"var","name":"t"}]}},
  "run_id": "inline-demo"
}
```

该式真值为 `false`（b 不满足 p）。

## 7. 退出码（CLI）

0 成功；2 输入错误；3 状态冲突（证明检查失败）；4 资源耗尽；5 计算失败。
