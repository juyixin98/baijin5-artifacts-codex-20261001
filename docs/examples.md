# 服务调用示例

假设服务已按 README 启动在 `127.0.0.1:8487`。以下命令与
`docs/evidence/server-session.txt` 中的实录一一对应。

## 1. 健康检查（含版本）

```bash
curl -s http://127.0.0.1:8487/v1/health
# {"name":"oci-layer-merge-checker","version":"0.1.0"}
```

## 2. 正常归并（自带请求身份）

```bash
curl -s -X POST http://127.0.0.1:8487/v1/merges \
  -H 'content-type: application/json' \
  -H 'x-request-id: req-demo-001' \
  -d '{"layers":["fixtures/layers/layer1","fixtures/layers/layer2","fixtures/layers/layer3"],
       "output_dir":"data/outputs/demo-good"}'
```

响应：`status: "success"`，`entries[]` 每项含 `source.index`（来源层）、
文件含 `sha256`/`size`；响应头回显 `x-request-id`。产物树与
`fixtures/expected/final-tree` 一致：`diff -r fixtures/expected/final-tree data/outputs/demo-good`。

## 3. 恶意层栈（失败与不确定结论单列）

```bash
curl -s -X POST http://127.0.0.1:8487/v1/merges \
  -H 'content-type: application/json' \
  -d '{"layers":["fixtures/malicious/layer-hardlink","fixtures/malicious/layer-escape",
                  "fixtures/malicious/layer-abslink","fixtures/malicious/layer-badwhiteout"],
       "output_dir":"data/outputs/demo-malicious"}'
```

响应：`status: "with_failures_and_uncertainties"`；
`failures[]`（`unsupported_hardlink`、`invalid_path`）与
`uncertainties[]`（`symlink_target_outside_root`、`absolute_symlink_unsupported`）
分列，各带路径与来源层。

## 4. 查询运行记录 / 人读报告

```bash
curl -s http://127.0.0.1:8487/v1/merges/<run_id>          # JSON 全量记录
curl -s http://127.0.0.1:8487/v1/merges/<run_id>/report   # 文本报告：步骤、来源、失败、不确定结论
```

## 5. 错误类别示例

```bash
# 层路径在工作区根之外 -> 400 outside_workspace
curl -s -X POST http://127.0.0.1:8487/v1/merges \
  -H 'content-type: application/json' \
  -d '{"layers":["/etc"],"output_dir":"data/outputs/never"}'

# 未知 run id -> 404 run_not_found
curl -s http://127.0.0.1:8487/v1/merges/run-nope
```
